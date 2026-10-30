#!/usr/bin/env python3
"""Review a project before a full check: what kind of app it is and what it uses.

Standard library only, read-only, no network. Reads the manifests (package.json,
requirements files, pyproject.toml, app.json) and a capped sample of the source in
the project folder and one level of sub-folders (frontend/, backend/, mobile/,
apps/*, packages/*), and reports signals with the files behind each one: web app,
installable PWA, API, React Native or native mobile, LLM use, on-device model,
retrieval (RAG), database, auth, voice, tests, email, translations.

    python3 review.py .
    python3 review.py ../web ../mobile --json
"""

import json
import os
import re
import sys

SKIP = {".git", "node_modules", ".next", ".expo", "dist", "build", "out", "coverage", "__pycache__", ".venv",
        "venv", "env", "proof", "Pods", ".gradle", "vendor", "target", ".turbo", ".cache", "graphify-out"}
SOURCE_EXT = {".js", ".jsx", ".ts", ".tsx", ".mjs", ".py", ".vue", ".svelte", ".html", ".kt", ".swift", ".java"}
MAX_FILES = 4000
MAX_BYTES = 300 * 1024

# signal -> dependency names (lower case) that show it
DEPS = {
    "web": {"next", "react-dom", "vite", "vue", "svelte", "@sveltejs/kit", "@angular/core", "astro", "nuxt",
            "@remix-run/react", "gatsby", "solid-js"},
    "pwa": {"next-pwa", "@ducanh2912/next-pwa", "workbox-webpack-plugin", "workbox-window", "vite-plugin-pwa",
            "@serwist/next", "serwist"},
    "api": {"fastapi", "flask", "django", "starlette", "express", "fastify", "koa", "@nestjs/core", "hono",
            "aiohttp", "sanic", "litestar", "@hapi/hapi"},
    "react_native": {"react-native", "expo"},
    "llm": {"openai", "anthropic", "@anthropic-ai/sdk", "langchain", "@langchain/core", "langchain-core",
            "llama-index", "llamaindex", "@google/generative-ai", "google-genai", "google-generativeai", "cohere",
            "mistralai", "@mistralai/mistralai", "ai", "litellm", "groq", "together", "ollama", "replicate"},
    "on_device_llm": {"llama.rn", "react-native-executorch", "@mlc-ai/web-llm", "mlc-llm", "@mediapipe/tasks-genai",
                      "onnxruntime-react-native", "react-native-transformers", "@react-native-ai/mlc",
                      "cactus-react-native"},
    "rag": {"pgvector", "pinecone", "pinecone-client", "@pinecone-database/pinecone", "chromadb", "qdrant-client",
            "@qdrant/js-client-rest", "weaviate-client", "faiss-cpu", "faiss-gpu", "lancedb", "llama-index",
            "@langchain/community", "sentence-transformers", "milvus", "pymilvus"},
    "database": {"@supabase/supabase-js", "supabase", "alembic", "sqlalchemy", "prisma", "@prisma/client",
                 "psycopg", "psycopg2", "psycopg2-binary", "asyncpg", "pg", "postgres", "drizzle-orm", "typeorm",
                 "sequelize", "mongoose", "mongodb", "pymongo", "knex", "kysely"},
    "auth": {"next-auth", "@auth/core", "passport", "@supabase/auth-helpers-nextjs", "@supabase/ssr",
             "firebase", "@clerk/nextjs", "@clerk/clerk-expo", "auth0", "@auth0/nextjs-auth0", "python-jose",
             "pyjwt", "passlib", "fastapi-users", "bcrypt", "jsonwebtoken", "@react-native-google-signin/google-signin",
             "expo-apple-authentication", "expo-auth-session", "lucia", "better-auth", "authlib"},
    "voice": {"react-native-webrtc", "livekit-client", "@livekit/react-native", "expo-av", "expo-audio",
              "@deepgram/sdk", "deepgram-sdk", "openai-whisper", "faster-whisper", "react-native-voice",
              "@react-native-voice/voice", "elevenlabs", "twilio-video", "agora-rtc-sdk-ng", "pipecat-ai"},
    "tests": {"pytest", "jest", "vitest", "mocha", "@playwright/test", "jest-expo", "cypress", "unittest2"},
    "email": {"sendgrid", "@sendgrid/mail", "resend", "nodemailer", "postmark", "mailgun", "@react-email/components",
              "fastapi-mail", "aiosmtplib", "boto3-ses"},
    "i18n": {"i18next", "react-i18next", "next-intl", "vue-i18n", "babel", "expo-localization", "react-intl",
             "lingui", "@lingui/core"},
}
# signal -> source patterns that show it when the dependencies do not
CODE = {
    "pwa": re.compile(r"serviceWorker\.register|rel=[\"']manifest[\"']"),
    "llm": re.compile(r"chat\.completions\.create|responses\.create|messages\.create\(|generateContent\("),
    "rag": re.compile(r"embeddings\.create|\bvector\s*\(|<->|cosine_distance|similarity_search"),
    "voice": re.compile(r"getUserMedia|RTCPeerConnection|SpeechRecognition|speechSynthesis"),
    "auth": re.compile(r"(?i)/(login|signin|sign-in|signup|sign-up|logout)\b|access_token|refresh_token"),
}
FILES = {
    "pwa": ("manifest.webmanifest", "manifest.json", "sw.js", "service-worker.js"),
    "database": ("alembic.ini", "schema.prisma"),
    "i18n": ("locales", "i18n", "lang"),
}
SIGNALS = ["web", "pwa", "api", "react_native", "native_mobile", "llm", "on_device_llm", "rag", "database", "auth",
           "voice", "tests", "email", "i18n"]
LABEL = {"web": "web app", "pwa": "installable PWA", "api": "API backend", "react_native": "React Native / Expo app",
         "native_mobile": "native mobile app", "llm": "calls an LLM", "on_device_llm": "runs a model on the device",
         "rag": "retrieval (RAG)", "database": "database", "auth": "sign-in", "voice": "voice or real-time audio",
         "tests": "a test suite", "email": "sends email", "i18n": "translations"}


def _read(path, limit=MAX_BYTES):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _deps_of(path):
    """Dependency names (lower case) declared by one manifest file."""
    name = os.path.basename(path).lower()
    text = _read(path)
    out = set()
    if name == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return out
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            out.update(k.lower() for k in (data.get(key) or {}))
    elif name.startswith("requirements") and name.endswith(".txt") or name == "pipfile":
        for line in text.splitlines():
            m = re.match(r"\s*([A-Za-z0-9_.\-\[\]]+)", line)
            if m and not line.strip().startswith(("#", "-")):
                out.add(re.sub(r"\[.*\]", "", m.group(1)).lower())
    elif name == "pyproject.toml":
        for m in re.finditer(r"[\"']([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*(?:[<>=!~;].*?)?[\"']", text):
            out.add(m.group(1).lower())
        for m in re.finditer(r"(?m)^([A-Za-z0-9_.\-]+)\s*=", text):
            out.add(m.group(1).lower())
    return out


def _roots(paths):
    """The given folders plus their immediate sub-folders that hold a manifest (monorepos)."""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        out.append(p)
        for parent in (p, os.path.join(p, "apps"), os.path.join(p, "packages")):
            try:
                names = sorted(os.listdir(parent))
            except OSError:
                continue
            for n in names:
                sub = os.path.join(parent, n)
                if n in SKIP or n.startswith(".") or not os.path.isdir(sub) or sub in out:
                    continue
                if any(os.path.isfile(os.path.join(sub, m)) for m in ("package.json", "requirements.txt", "pyproject.toml")):
                    out.append(sub)
    return out


def review(paths):
    """Return {"signals": {name: [evidence...]}, "kind": ..., "mobile": [...], "roots": [...], "files": n}."""
    found = dict((s, []) for s in SIGNALS)

    def add(sig, why):
        if why not in found[sig] and len(found[sig]) < 6:
            found[sig].append(why)

    roots = _roots(paths)
    platforms = set()
    for root in roots:
        for m in ("package.json", "requirements.txt", "requirements-dev.txt", "pyproject.toml", "Pipfile"):
            path = os.path.join(root, m)
            if not os.path.isfile(path):
                continue
            deps = _deps_of(path)
            rel = os.path.relpath(path, os.path.abspath(paths[0])).replace(os.sep, "/")
            for sig, names in DEPS.items():
                for d in sorted(deps & names):
                    add(sig, "%s: %s" % (rel, d))
        app_json = os.path.join(root, "app.json")
        if os.path.isfile(app_json) and '"expo"' in _read(app_json):
            add("react_native", os.path.relpath(app_json, os.path.abspath(paths[0])).replace(os.sep, "/"))
    scanned = 0
    base = os.path.abspath(paths[0])
    for p in paths:
        for cur, dirs, names in os.walk(os.path.abspath(p)):
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not d.startswith("."))
            rel_dir = os.path.relpath(cur, base).replace(os.sep, "/")
            if os.path.basename(cur) in FILES["i18n"] and any(n.endswith((".json", ".po", ".properties")) for n in names):
                add("i18n", rel_dir + "/")
            if os.path.basename(cur) == "migrations" and names:
                add("database", rel_dir + "/")
            for n in sorted(names):
                full = os.path.join(cur, n)
                rel = os.path.relpath(full, base).replace(os.sep, "/")
                if n in FILES["pwa"] and ("public" in rel.split("/") or "static" in rel.split("/") or rel.count("/") <= 2):
                    add("pwa", rel)
                if n in ("manifest.ts", "manifest.js") and "/app/" in "/" + rel:
                    add("pwa", rel)
                if n in FILES["database"]:
                    add("database", rel)
                if n == "AndroidManifest.xml" and "/build/" not in "/" + rel:
                    platforms.add("android")
                    add("native_mobile", rel)
                if n.endswith(".xcodeproj") or n == "Info.plist" and "/ios/" in "/" + rel:
                    platforms.add("ios")
                    add("native_mobile", rel)
                if n.startswith("test_") and n.endswith(".py") or n.endswith((".test.ts", ".test.tsx", ".test.js", ".spec.ts")):
                    add("tests", rel)
                if n in ("openapi.json", "openapi.yaml", "openapi.yml", "swagger.json"):
                    add("api", rel)
                if os.path.splitext(n)[1].lower() not in SOURCE_EXT or scanned >= MAX_FILES:
                    continue
                scanned += 1
                text = _read(full)
                for sig, rx in CODE.items():
                    m = rx.search(text)
                    if m:
                        add(sig, "%s: %s" % (rel, m.group(0)[:40]))
    if found["react_native"]:
        platforms.update(("android", "ios"))
        # react-dom alone comes with react-native-web; it is the same app, not a separate web app.
        found["web"] = [w for w in found["web"] if not w.endswith(": react-dom")]
        # React Native projects carry native folders; count them as the RN app, not a separate native one.
        found["native_mobile"] = []
    mobile = bool(found["react_native"] or found["native_mobile"])
    webish = bool(found["web"] or found["pwa"])
    if mobile and (webish or found["api"]):
        kind = "mixed"
    elif mobile:
        kind = "mobile"
    elif webish:
        kind = "web"
    elif found["api"]:
        kind = "api"
    else:
        kind = "repo"
    return {"signals": dict((s, v) for s, v in found.items() if v), "kind": kind,
            "mobile": sorted(platforms), "roots": [os.path.relpath(r, base).replace(os.sep, "/") for r in roots],
            "files": scanned}


def summary_lines(r):
    lines = ["Kind: %s%s" % (r["kind"], (" (mobile: %s)" % ", ".join(r["mobile"])) if r["mobile"] else "")]
    for s in SIGNALS:
        ev = r["signals"].get(s)
        if ev:
            lines.append("  %-26s %s" % (LABEL[s], "; ".join(ev[:2])))
    return lines


if __name__ == "__main__":
    args = [x for x in sys.argv[1:] if x != "--json"] or ["."]
    res = review(args)
    print(json.dumps(res, indent=2) if "--json" in sys.argv else "\n".join(summary_lines(res)))
