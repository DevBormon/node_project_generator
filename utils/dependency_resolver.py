"""Derive npm packages from TECH_STACK. No versions — yarn add resolves latest."""

import re


def _strip_version(s):
    """Remove version numbers from anywhere in the string."""
    s = re.sub(r'\s+\d+[\d.x*]*\s*(lts)?', ' ', s, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', s).lower().strip()


_SIMPLE = {
    "node.js":       None,
    "typescript":    ("typescript",           "devDependencies"),
    "express.js":    ("express",              "dependencies"),
    "express":       ("express",              "dependencies"),
    "fastify":       ("fastify",              "dependencies"),
    "nestjs":        ("@nestjs/core",         "dependencies"),
    "mysql":         ("mysql2",               "dependencies"),
    "postgresql":    ("pg",                   "dependencies"),
    "sqlite":        ("better-sqlite3",       "dependencies"),
    "mongodb":       ("mongodb",              "dependencies"),
    "typeorm":       ("typeorm",              "dependencies"),
    "prisma":        ("prisma",               "devDependencies"),
    "drizzle orm":   ("drizzle-orm",          "dependencies"),
    "sequelize":     ("sequelize",            "dependencies"),
    "memcached":     ("memjs",                "dependencies"),
    "redis":         ("redis",                "dependencies"),
    "kafka":         ("kafkajs",              "dependencies"),
    "rabbitmq":      ("amqplib",              "dependencies"),
    "bullmq":        ("bullmq",               "dependencies"),
    "zod":           ("zod",                  "dependencies"),
    "joi":           ("joi",                  "dependencies"),
    "pino":          ("pino",                 "dependencies"),
    "winston":       ("winston",              "dependencies"),
    "docker":        None,
    "github actions": None,
}

_COMPOUND = {
    "jwt (jose) + argon2": [
        ("jose",   "dependencies"),
        ("argon2", "dependencies"),
    ],
    "jwt (jsonwebtoken) + bcrypt": [
        ("jsonwebtoken", "dependencies"),
        ("bcrypt",      "dependencies"),
    ],
    "vitest + supertest": [
        ("vitest",              "devDependencies"),
        ("@vitest/coverage-v8", "devDependencies"),
        ("supertest",           "devDependencies"),
    ],
    "jest + supertest": [
        ("jest",      "devDependencies"),
        ("ts-jest",   "devDependencies"),
        ("supertest", "devDependencies"),
    ],
    "openapi + swagger ui": [
        ("swagger-ui-express", "dependencies"),
    ],
}

_CONDITIONAL = {
    "typeorm": {"dependencies": {"reflect-metadata"}},
    "prisma":  {"devDependencies": {"@prisma/client"}},
}

_ALWAYS = {
    "dependencies":    {"dotenv", "helmet", "cors", "express-rate-limit"},
    "devDependencies": {"@types/node", "tsx", "eslint", "@eslint/js", "typescript-eslint"},
}

_TYPES_MAP = {
    "express":              "@types/express",
    "cors":                 "@types/cors",
    "swagger-ui-express":   "@types/swagger-ui-express",
    "dotenv":               "@types/dotenv",
}


def resolve_dependencies(tech_stack: dict) -> dict:
    """Derive npm package names from TECH_STACK. No versions."""
    deps = {"dependencies": set(), "devDependencies": set()}

    for section, packages in _ALWAYS.items():
        deps[section].update(packages)

    for value in tech_stack.values():
        if not isinstance(value, str):
            continue
        normalized = _strip_version(value)

        matched = False
        for key, entries in _COMPOUND.items():
            if normalized == key or key in normalized or normalized in key:
                for name, section in entries:
                    deps[section].add(name)
                matched = True
                break

        if not matched and normalized in _SIMPLE:
            entry = _SIMPLE[normalized]
            if entry:
                deps[entry[1]].add(entry[0])

        if not matched:
            for key, entry in _SIMPLE.items():
                if key in normalized or normalized in key:
                    if entry:
                        deps[entry[1]].add(entry[0])
                    matched = True
                    break

        if normalized in _CONDITIONAL:
            for section, packages in _CONDITIONAL[normalized].items():
                deps[section].update(packages)

    for pkg_name, types_name in _TYPES_MAP.items():
        if pkg_name in deps["dependencies"]:
            deps["devDependencies"].add(types_name)

    return {k: sorted(v) for k, v in deps.items()}