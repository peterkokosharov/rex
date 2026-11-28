---
description: Java projects - JDK, Maven, Gradle, surefire reports.
detect: pom.xml, build.gradle, build.gradle.kts, settings.gradle, settings.gradle.kts, mvnw, gradlew
---
Java projects:

- Build tool: pom.xml = Maven, build.gradle(.kts) = Gradle. Prefer the wrapper (./mvnw, ./gradlew) when it
  exists. A root pom.xml with <modules> is multi-module: read the root pom, then each module's pom.
- In the pom read: the Java version (maven.compiler.release / source / target), <parent> (Spring Boot?),
  <dependencies> and their versions, plugins (surefire, failsafe, shade, spring-boot), <profiles>.
- JDK: `java -version` and `mvn -v` (or `gradle -v`) show the installed JDK; compare with the version the
  build asks for. A mismatch explains "invalid target release" and "class file has wrong version" errors.
- Layout: src/main/java (code), src/main/resources (application.properties/.yml, logback.xml),
  src/test/java (tests). Map packages to responsibilities before judging the design.
- Build and test: `mvn -q -o verify` (offline first; drop -o if a dependency is missing and network use is
  fine), one test with `mvn -q -o test -Dtest=ClassName`. Gradle: `./gradlew build --offline`, `./gradlew test`.
  Use a timeout of 600 seconds.
- Test failures: read target/surefire-reports/*.txt (summary) and the matching *.xml (stack trace) rather
  than the whole console log. Gradle: build/test-results/test/*.xml.
- Common failures: missing dependency offline (drop -o), wrong JDK, tests needing a database or network,
  "package does not exist" (module order or missing dependency), encoding warnings
  (project.build.sourceEncoding).
- Review points: swallowed exceptions or printStackTrace, System.out instead of a logger, string-concatenated
  SQL, resources not closed (use try-with-resources), mutable static state, null handling, very large classes,
  missing tests for business logic, outdated or duplicate dependencies.
