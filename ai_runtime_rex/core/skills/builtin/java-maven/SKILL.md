---
name: java-maven
description: Build, test or fix a Maven (or Gradle) Java project - JDK check, offline build, surefire reports, common build failures. Use when a Java build or test run is needed or fails.
---

1. **Find the build.** Root `pom.xml` (Maven; `mvnw` = use `./mvnw`) or `build.gradle(.kts)` (Gradle;
   `gradlew` = use `./gradlew`). For a multi-module pom read `<modules>` and each module's pom.
2. **Check the JDK.** Run `java -version` and `mvn -v` (or `./gradlew -v`). Read the version the build needs:
   `maven.compiler.release`, `maven.compiler.source/target`, `<release>` in maven-compiler-plugin, or the Gradle
   `toolchain`/`sourceCompatibility`. Report a mismatch before building.
3. **Build quietly, offline first** (timeout 600 s):
   - `mvn -q -o -B verify` (all), `mvn -q -o -B test -Dtest=ClassName[#method]` (one test),
     `mvn -q -o -B -pl module -am test` (one module with what it needs).
   - Gradle: `./gradlew build --offline -q`, `./gradlew test --tests 'ClassName'`.
   - If offline fails only because a dependency is missing, retry without `-o` / `--offline` when network
     use is acceptable; say that you did.
4. **Read the results, not the whole log.** Last ~40 lines of output first (`... 2>&1 | tail -n 40`). Test
   failures: `target/surefire-reports/*.txt` for the summary, the matching `TEST-*.xml` for the stack trace
   (`fileops grep` for `<failure` / `<error`). Gradle: `build/test-results/test/*.xml`.
5. **Common failures and their usual cause:**
   - `invalid target release` / `class file has wrong version` - JDK older than the build needs.
   - `Could not resolve dependencies` with `-o` - not in the local repository; build once online.
   - `package ... does not exist` - missing dependency or module built out of order (`-am`).
   - Tests need a database, network or a running service - name it; do not "fix" by skipping tests.
   - `There are test failures` - read the surefire report for the first failing test, then its source.
6. **Report:** command run, result (pass / N failures), the first real error with `path:line`, its cause and
   the fix (applied or proposed). Building writes `target/` or `build/`; never run it when the user asked for
   read-only work.
