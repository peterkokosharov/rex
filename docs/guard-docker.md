# Run the guard in Docker on Linux

rex itself runs on the host; only the tool-execution guard runs in a container (`guard/entrypoint.py`, image
`rex-guard:latest`). Tested layout: stock Linux (Linux Mint, Docker CE).

1. Build the image from the repository root:

   ```bash
   docker build -t rex-guard:latest .
   ```

   The tag must match `docker_guard.image` in the rex config (default `rex-guard:latest`).
   `docker compose build guard` builds the same image.

2. Your user must be allowed to talk to Docker: `sudo usermod -aG docker "$USER"`, then log out and in (or `newgrp docker`).
   Without it rex shows "permission denied while trying to connect to the docker API".

3. uid: the container runs as uid/gid 1000 (`guarduser`) and writes into the bind-mounted workspace. If your uid is
   not 1000 (`id -u`), files cannot be written. Either change `--user "1000:1000"` in `runtimelib/guard/__init__.py`
   and `user:` in `docker-compose.yml` to your `id -u`:`id -g`, or give uid 1000 write access to the workspace.

4. Network: `none` by default (`docker_guard.network_mode`). `GUARD_ALLOWED_DOMAINS` is only a policy inside the
   guard; it needs a network mode other than `none` to have any effect.

5. Optional LiteLLM proxy (compose profile `proxy`): copy `litellm_config.yaml.example` to `litellm_config.yaml`.

Not needed for quick development: `--local-tools` runs tools on the host without the guard.

Sources: `Dockerfile`, `docker-compose.yml`, `runtimelib/guard/__init__.py` in this repository, 2026-11-23.

In guard mode the container is the security boundary; rex does not apply the bash whitelist or deny-list as protection there.
