ai-rex notes

- ai-rex REQUIRES Git for Windows (Git Bash, https://git-scm.com/download/win): with --local-tools it runs every
  bash command with Git Bash, never cmd.exe.
- The program folder is called `rex` (front-ends such as Layka and Sisi look for that name); the command is
  rex\ai-rex.cmd. Try:  ai-rex.cmd --help
- Layka users: the installed folder %USERPROFILE%\apps\ai-rex contains `rex`; unzip-only use works too (payload\rex).
- rex_native.dll is optional; if it is missing or cannot be loaded, the pure Python fallback is used.
- Endpoint and key are normally passed by the front-end as AI_REX_LITELLM__BASE_URL and AI_REX_LITELLM__API_KEY.
