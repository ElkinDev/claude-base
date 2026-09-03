#!/bin/sh
# Wait for a marker file without letting the wait outlive the prompt cache.
#
# Why 270. The prompt cache entry lives five minutes and the window slides: every call that
# returns inside it renews the entry, and the next call reads the cached prefix instead of
# writing it again at the 1.25 rate. One call that blocks longer than five minutes lets the
# entry expire, so the turn after it pays a full context write, and a lane that waits in ten
# minute sleeps pays that on every loop. 270 seconds is the five minutes minus thirty for the
# round trip, which is why it is both the default and the ceiling: a caller that needs longer
# calls this again on exit 3 instead of asking for one longer sleep.
#
# usage: wait-marker.sh <file> [seconds]
#   exit 0  the file is there, nothing printed
#   exit 2  missing or non-numeric argument, usage on stderr
#   exit 3  the seconds ran out, one line on stderr
#
# The PowerShell twin beside this file answers the same contract.

CEILING=270
POLL=5

usage() {
    echo "usage: wait-marker.sh <file> [seconds]" >&2
    exit 2
}

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
    usage
fi

file=$1
[ -n "$file" ] || usage

seconds=${2:-$CEILING}
case $seconds in
    '' | *[!0-9]*) usage ;;
esac

# A number too long to compare is over the ceiling by definition, and the shell's arithmetic
# would overflow on it before the comparison could say so.
if [ ${#seconds} -gt 9 ] || [ "$seconds" -gt "$CEILING" ]; then
    echo "clamped to $CEILING s: no single wait may outlive the prompt cache" >&2
    seconds=$CEILING
fi

elapsed=0
while :; do
    if [ -e "$file" ]; then
        exit 0
    fi
    if [ "$elapsed" -ge "$seconds" ]; then
        echo "timeout after $seconds s: $file" >&2
        exit 3
    fi
    step=$POLL
    remaining=$((seconds - elapsed))
    if [ "$remaining" -lt "$step" ]; then
        step=$remaining
    fi
    sleep "$step"
    elapsed=$((elapsed + step))
done
