<!--
Copyright (c) 2026 Peter Kokosharov <peter.kokosharov@gmail.com>
LLM: claude-opus-5-5
Licensed under MIT License
-->

# AGENTS.md template

Copy the block below into `AGENTS.md` at the root of your project and fill it in. ai-rex reads it when the
workspace is trusted (`workspace.trust: true` or `AI_REX_WORKSPACE__TRUST=true`); other agents that know the
AGENTS.md convention read it too. Keep it short: facts the agent cannot cheaply find itself. Lines starting with
`===` or `---` are quoted by ai-rex, so use `#` headings. Up to 16 KB is read.

How the file is used: see [AGENT-PROFILES.md](AGENT-PROFILES.md). It comes after the instruction layers and the
skill index; it never overrides the permission rules.

```markdown
# AGENTS.md

<!-- ai-rex-profile: programming/java -->
<!-- optional: pick instruction layers instead of auto-detection, e.g. programming/python or auto,programming/java -->

## Project
One or two sentences: what this is, who uses it, the main technology (e.g. "Order service, Java 17,
Spring Boot 3, PostgreSQL").

## Build and test
- Build: `mvn -q -o verify`
- One test: `mvn -q -o test -Dtest=OrderServiceTest`
- Run locally: `mvn spring-boot:run -Dspring-boot.run.profiles=local`
- Lint / format: `mvn spotless:check`
- Needs: JDK 17, a local PostgreSQL on 5432 for integration tests (`-Pit`)

## Layout
- `src/main/java/com/example/order/api/` - REST controllers
- `src/main/java/com/example/order/domain/` - business logic, no framework code
- `src/main/resources/db/migration/` - Flyway migrations (never edit an applied one)
- `docs/` - architecture notes

## Conventions
- Logging: SLF4J with `{}` placeholders, never System.out or string concatenation
- Tests live in the package of the class they test; names end in `Test` (unit) or `IT` (integration)
- Commit messages: `TICKET-123 short summary`

## Do not touch
- `generated/`, `target/`, `*.lock` files
- `src/main/resources/application-prod.yml`
- Do not push, do not change the version in pom.xml

## Known problems
- `LegacyImportJob` is slow and untested; leave it unless asked
- Integration tests fail without the database; unit tests do not need it
```
