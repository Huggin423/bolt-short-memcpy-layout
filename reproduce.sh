#!/usr/bin/env bash
set -euo pipefail

asset=BOLT-Docker-linux-amd64.tar.gz
url="https://github.com/Huggin423/bolt-short-memcpy-layout/releases/download/v1.0/$asset"
archive_sha=efc3430b35abb49e5ad71110593f3d6dba21a9329cbc9bf6d167c3c39cf08d3c
image=sha256:6c7fe2c740a5b63978ce8d5ab11800b1ce449a6891ab1dbeb04e7c8a623e50f9
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
archive=${BOLT_ARCHIVE:-}
temp=
container=
cleanup() {
    if [[ -n "$container" ]]; then docker rm "$container" >/dev/null || true; fi
    if [[ -n "$temp" ]]; then rm -rf -- "$temp"; fi
}
trap cleanup EXIT

usage() {
    cat <<'USAGE'
Usage:
  bash reproduce.sh [smoke]
  bash reproduce.sh cpus
  bash reproduce.sh bench --cpu N --out DIR [--blocks N]
  bash reproduce.sh extract DIR

BOLT_ARCHIVE=/path/to/BOLT-Docker-linux-amd64.tar.gz uses a local copy.
USAGE
}
die() { printf '%s\n' "$*" >&2; exit 1; }
empty_dir() {
    mkdir -p -- "$1" || return
    local dir entry
    dir=$(cd -- "$1" && pwd -P) || return
    entry=$(find "$dir" -mindepth 1 -maxdepth 1 -print -quit) || return
    [[ -z "$entry" ]] ||
        die "Choose an empty directory: $dir"
    printf '%s\n' "$dir"
}

mode=${1:-smoke}
if [[ $# -gt 0 ]]; then shift; fi
case "$mode" in
    -h|--help) usage; exit 0 ;;
    smoke|cpus) [[ $# == 0 ]] || die "Unexpected argument for $mode" ;;
    extract) [[ $# == 1 && -n "$1" ]] || die "Usage: bash reproduce.sh extract DIR" ;;
    bench)
        cpu= out= blocks=144
        while [[ $# -gt 0 ]]; do
            [[ $# -ge 2 && -n "$2" ]] || die "Missing value for $1"
            case "$1" in
                --cpu) cpu=$2 ;;
                --out) out=$2 ;;
                --blocks) blocks=$2 ;;
                *) die "Unknown bench option: $1" ;;
            esac
            shift 2
        done
        [[ "$cpu" =~ ^[0-9]+$ && "$blocks" =~ ^[1-9][0-9]*$ && -n "$out" ]] ||
            die "Usage: bash reproduce.sh bench --cpu N --out DIR [--blocks N]"
        ;;
    *) usage >&2; exit 2 ;;
esac

command -v docker >/dev/null || die 'Docker is required.'
platform=$(docker info --format '{{.OSType}}/{{.Architecture}}') ||
    die 'Could not contact the Docker engine.'
case "$platform" in
    linux/x86_64|linux/amd64) ;;
    *) die 'Use a native x86-64 Linux Docker engine.' ;;
esac
if [[ -z "$archive" && -f "$root/$asset" ]]; then archive="$root/$asset"; fi
if [[ -n "$archive" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
    if [[ -z "$archive" ]]; then
        command -v curl >/dev/null || die 'Install curl or set BOLT_ARCHIVE.'
        temp=$(mktemp -d)
        archive="$temp/$asset"
        curl --fail --location --show-error --silent --retry 2 --connect-timeout 15 \
            --output "$archive" "$url"
    fi
    [[ -f "$archive" ]] || die "Archive not found: $archive"
    actual=$(sha256sum < "$archive")
    [[ "${actual%% *}" == "$archive_sha" ]] || die "SHA-256 mismatch: $archive"
    printf 'PASS archive SHA-256 %s\n' "$archive_sha"
    docker load -i "$archive"
fi
[[ "$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")" == linux/amd64 ]] ||
    die 'The expected linux/amd64 Release image is missing.'

run=(docker run --pull never --rm --network none --read-only --tmpfs /tmp:rw,exec)
case "$mode" in
    smoke)
        "${run[@]}" "$image" smoke
        ;;
    cpus)
        "${run[@]}" --entrypoint python3 "$image" \
            -c 'import os; print(sorted(os.sched_getaffinity(0)))'
        ;;
    bench)
        out=$(empty_dir "$out")
        "${run[@]}" --volume "$out:/results" "$image" \
            bench --cpu "$cpu" --blocks "$blocks" --out /results
        ;;
    extract)
        out=$(empty_dir "$1")
        container=$(docker create "$image")
        docker cp "$container:/course/." "$out/"
        ;;
esac
