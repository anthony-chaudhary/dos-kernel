# Root Dockerfile — the auto-detected build for hosted-MCP registries (Glama,
# and any platform that builds the repo's root `Dockerfile` to run the server).
# It is byte-equivalent in EFFECT to docker/Dockerfile (the canonical one, kept
# for the ghcr publish workflow); a root Dockerfile can't `include` another, so
# the recipe is duplicated rather than referenced. Keep the two in sync — both
# are smoke-tested by .github/workflows/docker-publish.yml.
#
#   docker build -t dos-kernel .
#   docker run --rm -i dos-kernel        # the MCP server on stdio (default CMD)
#   docker run --rm -v "$PWD:/work" dos-kernel dos doctor --workspace /work

FROM python:3.12-slim

# git is not optional: every truth verdict reads git ancestry. And a mounted
# /work is owned by the HOST uid, which git refuses by default ("dubious
# ownership") — a single-purpose container trusts its mounts, so widen it here
# or the headline `-v $PWD:/work` use breaks on most hosts.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git config --system safe.directory '*'

COPY . /src
RUN pip install --no-cache-dir "/src[mcp]"

# The kernel adjudicates the workspace MOUNTED at /work, never its own image.
WORKDIR /work

# No ENTRYPOINT, so `docker run IMAGE dos verify …` reads as written. The
# default command is the MCP server on stdio — what a hosted registry's
# introspection check (and an MCP client launching the server) starts.
CMD ["dos-mcp"]
