#!/bin/bash
# Starts both privileged helpers (cron + processes) and stops both on ^C,
# or as soon as either one exits (e.g. it failed to start).
set -u

project_dir=/home/jgadmin/Senior-Project
python_bin=$project_dir/.venv/bin/python3.14

# Re-run this whole script as root once, so the helpers need no sudo of their own
# and this script is allowed to signal them.
if [ "$EUID" -ne 0 ]; then
    exec sudo "$0" "$@"
fi

helper_pids=()
# setsid keeps the helpers out of the terminal's process group, so ^C reaches only this
# script, which then sends each helper exactly one SIGTERM (a second signal force-exits them).
for helper_script in AdminCron.py AdminProcesses.py; do
    setsid "$python_bin" "$project_dir/src/privileged/$helper_script" &
    helper_pids+=($!)
done

stopHelpers() {
    trap - INT TERM
    kill -TERM "${helper_pids[@]}" 2>/dev/null
    wait "${helper_pids[@]}"
}
trap 'stopHelpers; exit 130' INT
trap 'stopHelpers; exit 143' TERM

wait -n "${helper_pids[@]}"
exit_status=$?
stopHelpers
exit $exit_status
