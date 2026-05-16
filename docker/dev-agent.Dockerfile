# =====================================================================
# SHIELD dev-agent container.
#
# A sandboxed Claude Code agent that operates ONLY inside this container.
# The host filesystem is not mounted; only the SHIELD repo is, read-write,
# at /workspace.
#
# Hardening (also enforced by docker-compose):
#   - non-root user
#   - read-only root filesystem (compose: read_only: true) + tmpfs /tmp
#   - cap_drop: ALL
#   - no-new-privileges
#   - no published ports; internal network only
#
# Inside this container the agent uses --dangerously-skip-permissions
# because the BLAST RADIUS is the container itself, not your machine.
# =====================================================================
FROM node:20-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        python3 \
        python3-pip \
        python3-venv \
        ripgrep \
        jq \
        make \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 1000 agent \
 && useradd  --uid 1000 --gid agent -d /home/agent -m -s /bin/bash agent \
 && mkdir -p /workspace /home/agent/.claude \
 && chown -R agent:agent /workspace /home/agent

# Install the Claude Code CLI globally as root so the agent user can run it.
RUN npm install -g @anthropic-ai/claude-code

USER agent
WORKDIR /workspace

# Entry script is mounted from the repo at /workspace/scripts/dev_agent_entry.sh
ENTRYPOINT ["/bin/bash", "/workspace/scripts/dev_agent_entry.sh"]
