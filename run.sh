#!/bin/sh
# Run script for Wyoming BlueTTS add-on
set -e

CONFIG_PATH=/data/options.json

# `languages`/`voices` are lists; read them as comma-separated strings with jq.
read_list() {
    jq -r --arg k "$1" 'if (.[$k] | type) == "array" then (.[$k] | join(","))
           elif (.[$k] | type) == "string" then .[$k]
           else "" end' "$CONFIG_PATH" 2>/dev/null
}

# Home Assistant Supervisor writes /data/options.json for every app
# regardless of whether bashio is available -- and it never is here, since
# this project doesn't build from an HA base image (see AGENTS.md's "No
# build.yaml, on purpose" note). Read it directly with jq; fall back to
# plain env vars for standalone Docker.
if [ -f "$CONFIG_PATH" ]; then
    LANGUAGES=$(read_list languages)
    DEFAULT_LANGUAGE=$(jq -r '.default_language // "en"' "$CONFIG_PATH")
    VOICES=$(read_list voices)
    VOICES_DIR=$(jq -r '.voices_dir // "/share/tts-voices"' "$CONFIG_PATH")
    MODELS_DIR=$(jq -r '.models_dir // "/data/models"' "$CONFIG_PATH")
    DEBUG=$(jq -r '.debug // false' "$CONFIG_PATH")
    # `// true` doesn't work here: jq's `//` treats `false` (not just `null`
    # /missing) as "no value" and falls through to the alternative, which
    # would silently ignore a user explicitly disabling this in the app's
    # Configuration tab. `debug`'s `// false` above doesn't hit this in
    # practice (fallback matches what an explicit `false` should produce
    # anyway), but a default-true option needs the explicit has()/null check.
    SPEAK_DECIMAL_POINTS=$(jq -r \
        'if (has("speak_decimal_points") and (.speak_decimal_points != null))
         then .speak_decimal_points else true end' "$CONFIG_PATH")
else
    # Defaults for standalone usage (also overridable via plain env vars,
    # e.g. from docker-compose.yml)
    LANGUAGES="${LANGUAGES:-en,es,de,it}"
    DEFAULT_LANGUAGE="${DEFAULT_LANGUAGE:-en}"
    VOICES="${VOICES:-}"
    VOICES_DIR="${VOICES_DIR:-/share/tts-voices}"
    MODELS_DIR="${MODELS_DIR:-/data/models}"
    DEBUG="${DEBUG:-false}"
    SPEAK_DECIMAL_POINTS="${SPEAK_DECIMAL_POINTS:-true}"
fi

[ "$LANGUAGES" = "null" ] && LANGUAGES="en,es,de,it"
[ "$VOICES" = "null" ] && VOICES=""

mkdir -p "$VOICES_DIR" "$MODELS_DIR"

# POSIX sh has no arrays -- positional parameters via `set --` instead, so
# this script runs under any /bin/sh (busybox ash on Alpine, dash/bash
# elsewhere) without requiring bash to be installed just for this.
set -- \
    --host "0.0.0.0" \
    --port "10200" \
    --languages "$LANGUAGES" \
    --default-language "$DEFAULT_LANGUAGE" \
    --voices "$VOICES" \
    --voices-dir "$VOICES_DIR" \
    --models-dir "$MODELS_DIR"

if [ "$DEBUG" = "true" ]; then
    set -- "$@" --debug
fi

if [ "$SPEAK_DECIMAL_POINTS" = "false" ]; then
    set -- "$@" --no-speak-decimal-points
fi

echo "========================================"
echo "Wyoming BlueTTS Server"
echo "========================================"
echo "Languages: $LANGUAGES (default: $DEFAULT_LANGUAGE)"
echo "Voices: ${VOICES:-<all built-in + custom (on demand)>}"
echo "Voices dir: $VOICES_DIR"
echo "Models dir: $MODELS_DIR"
echo "Debug: $DEBUG"
echo "Speak decimal points: $SPEAK_DECIMAL_POINTS"
echo "========================================"

# Function to send discovery info to Home Assistant
send_discovery() {
    # Wait for the server to be ready (up to 10 minutes for first model download)
    local max_wait=600
    local waited=0
    echo "Waiting for Wyoming server to be ready for discovery..."

    while [ $waited -lt $max_wait ]; do
        if echo '{"type":"describe"}' | nc -w 2 localhost 10200 2>/dev/null | grep -q "bluetts"; then
            echo "Server is ready after ${waited}s"
            break
        fi
        sleep 2
        waited=$((waited + 2))
    done

    if [ $waited -ge $max_wait ]; then
        echo "Warning: Timed out waiting for server to start for discovery"
        return 1
    fi

    # Small delay to ensure server is fully ready
    sleep 1

    # Check if running in Home Assistant (supervisor API available)
    if [ -n "$SUPERVISOR_TOKEN" ]; then
        local hostname discovery_host ipv4
        # Get hostname and convert underscores to hyphens for valid DNS name
        hostname=$(hostname | tr '_' '-')

        # Prefer advertising our IPv4 address over the hostname. The hassio
        # network is dual-stack, so the add-on hostname resolves to BOTH an
        # IPv4 and an IPv6 (ULA) address. Home Assistant Core resolves the
        # IPv6 first; on hosts with IPv6 disabled (the common case) that
        # address is unroutable, so Core's connection attempt hangs on a
        # dropped SYN and times out ("Unable to connect" -> the TTS entity
        # stays stuck "Initialising"). Advertising the IPv4 address sidesteps
        # the broken IPv6 path entirely. Fall back to the hostname if we
        # cannot determine an IPv4 address.
        ipv4=$(hostname -i 2>/dev/null | tr ' ' '\n' \
            | grep -E '^(10\.|192\.168\.|172\.(1[6-9]|2[0-9]|3[01])\.)' | head -n1)
        if [ -z "$ipv4" ]; then
            ipv4=$(getent ahostsv4 "$hostname" 2>/dev/null | awk '{print $1; exit}')
        fi
        if [ -n "$ipv4" ]; then
            discovery_host="$ipv4"
            echo "Advertising IPv4 address ${ipv4} for discovery (avoids unreachable IPv6 on IPv6-disabled hosts)"
        else
            discovery_host="$hostname"
            echo "Could not determine IPv4 address; falling back to hostname ${hostname} for discovery"
        fi
        echo "Sending discovery for host: ${discovery_host}:10200"

        # Retry discovery up to 3 times
        local retry=0
        local max_retries=3
        while [ $retry -lt $max_retries ]; do
            local response
            response=$(curl -s -X POST \
                -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
                -H "Content-Type: application/json" \
                -d "{\"service\": \"wyoming\", \"config\": {\"uri\": \"tcp://${discovery_host}:10200\"}}" \
                "http://supervisor/discovery" 2>&1)

            if echo "$response" | grep -q '"result".*"ok"'; then
                echo "Successfully sent discovery information to Home Assistant"
                return 0
            else
                echo "Discovery attempt $((retry + 1)) response: $response"
                retry=$((retry + 1))
                sleep 2
            fi
        done
        echo "Warning: Failed to send discovery after ${max_retries} attempts"
    else
        echo "Not running in Home Assistant (no SUPERVISOR_TOKEN) - skipping discovery"
    fi
}

# Start discovery in background (will wait for server to be ready)
send_discovery &

# Run the server (packages installed to system Python)
exec python3 -m wyoming_bluetts "$@"
