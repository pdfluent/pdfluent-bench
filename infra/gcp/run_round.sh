#!/usr/bin/env bash
# Copyright (c) 2026 Innovation Trigger B.V. All rights reserved.
# Licence pending owner decision: see LICENSE-TBD.md.
#
# A benchmark round on GCP: one spot VM per machine type, project `pdfluent`,
# region europe-west4. THIS SPENDS MONEY. Without CONFIRM=yes it only prints
# the plan, the cost ceiling and the commands it would run.
#
#   MACHINES="c3-standard-8 g2-standard-8" CONFIGS=all SETS=all \
#   SPLITS=tuning,holdout BECAUSE="monthly round" BUDGET_EUR=20 \
#   infra/gcp/run_round.sh                 # plan only
#   CONFIRM=yes infra/gcp/run_round.sh     # the round
#
# COST GUARD. For each machine the VM lifetime is capped (--max-run-duration,
# deleted by GCP when it runs out) at the modelled round time x 1.25, or at
# HOURS when given. The ceiling is that lifetime at the ON-DEMAND price plus
# disk (configs/machines.yaml); the sum over the machines must not exceed
# BUDGET_EUR (default 20), or nothing is created.
#
# TEARDOWN. Every VM is deleted when this script exits, however it exits
# (trap), and GCP deletes it at the lifetime cap if this script is gone.
#
# The VM gets this repository as a git bundle (no remote is involved), builds
# the images, fetches the datasets from their public sources, runs
# `make bench RUNNER=docker`, and the results directory is copied back to
# results/. Nothing else leaves the VM.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 1

PROJECT="${PROJECT:-pdfluent}"
REGION="${REGION:-europe-west4}"
MACHINES="${MACHINES:-c3-standard-8}"
CONFIGS="${CONFIGS:-all}"
SETS="${SETS:-all}"
SPLITS="${SPLITS:-tuning,holdout}"
PROFILE="${PROFILE:-full}"
REPS="${REPS:-3}"
BECAUSE="${BECAUSE:-}"
BUDGET_EUR="${BUDGET_EUR:-20}"
CONFIRM="${CONFIRM:-no}"
POLL_S="${POLL_S:-120}"        # how often the VM is asked whether the round ended
SYNC_EVERY="${SYNC_EVERY:-10}" # every this many polls, results/ is copied back
PY="${PY:-python3}"
# What each machine measures. Quality is measured on the CPU machine; a GPU
# machine adds the speed column only, unless GPU_QUALITY=yes. SPEED=no makes a
# quality-only shard, QUALITY=no a speed-only one.
QUALITY="${QUALITY:-yes}"
SPEED="${SPEED:-yes}"
GPU_QUALITY="${GPU_QUALITY:-no}"
# Images for the VMs. The script checks that each family exists before it
# creates anything (gcloud compute images describe-from-family). To list them:
#   gcloud compute images list --project deeplearning-platform-release --no-standard-images --format='value(family)'
# common-cu124-debian-11-py310, the earlier default, no longer exists.
CPU_IMAGE_PROJECT="${CPU_IMAGE_PROJECT:-debian-cloud}"
CPU_IMAGE_FAMILY="${CPU_IMAGE_FAMILY:-debian-12}"
GPU_IMAGE_PROJECT="${GPU_IMAGE_PROJECT:-deeplearning-platform-release}"
GPU_IMAGE_FAMILY="${GPU_IMAGE_FAMILY:-common-cu129-ubuntu-2404-nvidia-580}"

case ",$SPLITS," in
  *,holdout,*) [ -n "$BECAUSE" ] || { echo "REFUSED: SPLITS includes holdout; set BECAUSE to the reason" >&2; exit 2; } ;;
esac

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
LOGS="$ROOT/work/gcp/$stamp"
mkdir -p "$LOGS"

zone_of() { "$PY" -c "import sys; sys.path.insert(0, '.'); from bench import configs; print(configs.machine(sys.argv[1])['zone'])" "$1"; }
disk_of() { "$PY" -c "import sys; sys.path.insert(0, '.'); from bench import configs; print(configs.machine(sys.argv[1]).get('disk_gb', 100))" "$1"; }
gpu_of()  { "$PY" -c "import sys; sys.path.insert(0, '.'); from bench import configs; print(configs.machine(sys.argv[1]).get('gpu') or '')" "$1"; }
# The `bench run` flags for a machine: what it measures.
measure_flags() {
  local gpu="$1" flags=""
  if [ "$QUALITY" = no ] || { [ -n "$gpu" ] && [ "$GPU_QUALITY" != yes ]; }; then flags="--no-quality"; fi
  [ "$SPEED" = no ] && flags="$flags --no-speed"
  case "$flags" in *--no-quality*--no-speed*) echo "REFUSED: this machine would measure nothing" >&2; return 1;; esac
  echo "${flags# }"
}

# ---- the images exist, before anything is created ---------------------------
check_family() {
  local project="$1" family="$2" found
  if ! command -v gcloud > /dev/null; then
    [ "$CONFIRM" = yes ] && { echo "FATAL: gcloud is not installed" >&2; exit 2; }
    echo "image family $project/$family: not checked (no gcloud here)"
    return 0
  fi
  if ! found="$(gcloud compute images describe-from-family "$family" --project="$project" \
                  --format='value(name,status)' 2>&1)"; then
    echo "REFUSED: image family $project/$family does not exist or cannot be read; nothing was created" >&2
    echo "$found" | tail -n 2 >&2
    exit 3
  fi
  echo "image family $project/$family: $found"
}
for m in $MACHINES; do
  if [ -n "$(gpu_of "$m")" ]; then check_family "$GPU_IMAGE_PROJECT" "$GPU_IMAGE_FAMILY"
  else check_family "$CPU_IMAGE_PROJECT" "$CPU_IMAGE_FAMILY"; fi
done

# ---- the cost guard, before anything exists ---------------------------------
total=0
for m in $MACHINES; do
  flags="$(measure_flags "$(gpu_of "$m")")" || exit 3
  args=(--machine "$m" --configs "$CONFIGS" --sets "$SETS" --splits "$SPLITS" --profile "$PROFILE"
        --reps "$REPS" --budget "$BUDGET_EUR")
  # shellcheck disable=SC2206 # the flags are words
  args+=($flags)
  [ -n "${HOURS:-}" ] && args+=(--hours "$HOURS")
  out="$("$PY" -m bench cost "${args[@]}")" || { echo "$out"; echo "REFUSED by the cost guard for $m" >&2; exit 3; }
  echo "$out" | tee "$LOGS/cost-$m.txt"
  echo "$out" | sed -n 's/^max_hours=//p' > "$LOGS/hours-$m"
  eur="$(echo "$out" | sed -n 's/^ceiling: EUR \([0-9.]*\).*/\1/p')"
  total="$("$PY" -c "print(round($total + $eur, 2))")"
done
echo "=== ceiling over all machines: EUR $total (budget EUR $BUDGET_EUR)"
if ! "$PY" -c "import sys; sys.exit(0 if $total <= $BUDGET_EUR else 1)"; then
  echo "REFUSED: the summed ceiling EUR $total exceeds BUDGET_EUR $BUDGET_EUR; nothing was created" >&2
  exit 3
fi

# ---- what each VM runs --------------------------------------------------------
remote_script() {
  local gpu="$1" flags
  flags="$(measure_flags "$gpu")"
  cat <<REMOTE
set -euo pipefail
# The round runs detached from the ssh session that started it (a dropped
# connection or a sleeping laptop must not end it); ~/round.done says it ended.
trap 'echo "\$?" > ~/round.done' EXIT
sudo bash /tmp/vm_setup.sh $([ -n "$gpu" ] && echo gpu || echo cpu)
git clone -q ~/bench.bundle ~/pdfluent-bench
cd ~/pdfluent-bench || exit 1
families="pdfluent docling pymupdf4llm"
[ -n "$gpu" ] && families="docling-gpu"
# The best tier's weights and engine builds are not public; its image is built
# only when they are provided (BUILD_BEST=1 and the variables in configs/pdfluent-best.yaml).
[ "${BUILD_BEST:-0}" = 1 ] && [ -z "$gpu" ] && families="\$families best"
sudo docker/build.sh \$families
# The datasets are fetched inside the first image: it has the fetch tools.
sudo python3 -m bench datasets fetch --sets $SETS --profile $PROFILE --work work --in-image \${families%% *}
sudo env BENCH_INSTANCE="\$(cat /tmp/instance)" make bench MACHINE=\$(cat /tmp/machine) CONFIGS=$CONFIGS SETS=$SETS SPLITS=$SPLITS \\
  PROFILE=$PROFILE REPS=$REPS RUNNER=docker BECAUSE="$BECAUSE" OPTIONAL=pdfluent-best PY=python3 \\
  EXTRA="$flags" || echo "make bench exited non-zero; run.json lists the failed steps"
# What is inside each image: the frozen packages and, for pdfluent, the
# installed models and their verified pins. No dataset content.
sudo mkdir -p work/freeze
for f in \$families; do
  tag="\$(python3 -c 'import json,sys; print(json.load(open("work/images.json"))[sys.argv[1]]["tag"])' "\$f")"
  sudo docker run --rm "\$tag" sh -c 'for x in /opt/ex/*/FREEZE.txt; do echo "== \$x"; cat "\$x"; done; for x in /opt/models.cli-version.txt /opt/models/STATUS.txt /opt/models/SHA256SUMS.installed; do [ -f "\$x" ] && { echo "== \$x"; cat "\$x"; }; done; true' 2>&1 | sudo tee "work/freeze/\$f.txt" > /dev/null || true
done
sudo tar czf ~/results.tgz results work/images.json work/freeze
REMOTE
}

run_machine() {
  local m="$1" zone disk gpu name hours
  zone="$(zone_of "$m")"; disk="$(disk_of "$m")"; gpu="$(gpu_of "$m")"
  hours="$(cat "$LOGS/hours-$m")"
  name="pdfluent-bench-$(echo "$m" | tr -c 'a-z0-9\n' '-')-$(echo "$stamp" | tr 'TZ' 'tz')"
  local create=(gcloud compute instances create "$name" --project="$PROJECT" --zone="$zone"
    --machine-type="$m" --provisioning-model=SPOT --instance-termination-action=DELETE
    --max-run-duration="${hours}h" --boot-disk-size="${disk}GB" --boot-disk-type=pd-balanced
    --labels="purpose=pdfluent-bench,run=$(echo "$stamp" | tr 'TZ' 'tz')" --scopes=default)
  if [ -n "$gpu" ]; then
    # g2-standard-8 carries its one L4 by machine type; GPUs need TERMINATE on maintenance.
    create+=(--maintenance-policy=TERMINATE --image-project="$GPU_IMAGE_PROJECT"
             --image-family="$GPU_IMAGE_FAMILY" --metadata=install-nvidia-driver=True)
  else
    create+=(--image-project="$CPU_IMAGE_PROJECT" --image-family="$CPU_IMAGE_FAMILY")
  fi
  local ssh=(gcloud compute ssh "$name" --project="$PROJECT" --zone="$zone" --quiet)
  local scp=(gcloud compute scp --project="$PROJECT" --zone="$zone" --quiet)
  if [ "$CONFIRM" != yes ]; then
    echo "--- $m: would run (lifetime cap ${hours} h):"
    printf '  %q' "${create[@]}"; echo
    echo "  git bundle create work/gcp/$stamp/bench.bundle HEAD"
    echo "  ${scp[*]} <bundle> infra/gcp/vm_setup.sh $name:~/ ; then, detached (nohup), the remote script below"
    remote_script "$gpu" | sed 's/^/    /'
    echo "  every ${POLL_S}s: is ~/round.done there; every $((POLL_S * SYNC_EVERY))s: copy results/ back as $m-partial.tgz"
    echo "  ${scp[*]} $name:~/results.tgz work/gcp/$stamp/$m-results.tgz"
    echo "  gcloud compute instances delete $name --project=$PROJECT --zone=$zone --quiet"
    return 0
  fi
  # From here on the VM exists until the trap or the lifetime cap deletes it.
  trap 'gcloud compute instances delete "'"$name"'" --project="'"$PROJECT"'" --zone="'"$zone"'" --quiet || echo "TEARDOWN FAILED for '"$name"': delete it by hand" >&2' EXIT
  "${create[@]}" > "$LOGS/$m-create.log" 2>&1
  for _ in $(seq 1 30); do "${ssh[@]}" --command=true > /dev/null 2>&1 && break; sleep 10; done
  git bundle create "$LOGS/bench.bundle" HEAD > /dev/null
  "${scp[@]}" "$LOGS/bench.bundle" "$name:~/bench.bundle"
  "${scp[@]}" infra/gcp/vm_setup.sh "$name:/tmp/vm_setup.sh"
  "${ssh[@]}" --command="echo $m > /tmp/machine; echo $name > /tmp/instance"
  remote_script "$gpu" > "$LOGS/$m-remote.sh"
  "${scp[@]}" "$LOGS/$m-remote.sh" "$name:~/round.sh"
  "${ssh[@]}" --command="nohup bash ~/round.sh > ~/round.log 2>&1 < /dev/null & echo started"
  # Poll. A dropped ssh is retried; an instance that is gone (spot preemption,
  # lifetime cap) ends the wait, and the last partial copy is what is left.
  local polls=0 state
  while :; do
    sleep "$POLL_S"
    polls=$((polls + 1))
    state="$("${ssh[@]}" --command="cat ~/round.done 2>/dev/null || echo running" 2>/dev/null | tail -n 1)" || state=""
    if [ -z "$state" ]; then
      if ! gcloud compute instances describe "$name" --project="$PROJECT" --zone="$zone" \
             --format='value(status)' > "$LOGS/$m-status.txt" 2>&1; then
        echo "LOST: $name is gone (spot preemption or lifetime cap); last partial copy: $LOGS/$m-partial.tgz" >&2
        return 1
      fi
      echo "$(date -u +%FT%TZ) $m: ssh failed, instance $(cat "$LOGS/$m-status.txt"); retrying" >> "$LOGS/$m-poll.log"
      continue
    fi
    echo "$(date -u +%FT%TZ) $m: $state" >> "$LOGS/$m-poll.log"
    [ "$state" != running ] && break
    if [ $((polls % SYNC_EVERY)) -eq 0 ]; then
      "${ssh[@]}" --command="cd ~/pdfluent-bench 2>/dev/null && sudo tar czf ~/partial.tgz results 2>/dev/null && sudo chmod a+r ~/partial.tgz" > /dev/null 2>&1 \
        && "${scp[@]}" "$name:~/partial.tgz" "$LOGS/$m-partial.tgz.new" > /dev/null 2>&1 \
        && mv "$LOGS/$m-partial.tgz.new" "$LOGS/$m-partial.tgz" || true
    fi
  done
  "${scp[@]}" "$name:~/round.log" "$LOGS/$m-round.log" || true
  [ "$state" = 0 ] || echo "round on $m exited $state; see $LOGS/$m-round.log" >&2
  "${scp[@]}" "$name:~/results.tgz" "$LOGS/$m-results.tgz" || echo "no results copied from $m" >&2
  mkdir -p "$ROOT/results"
  [ -f "$LOGS/$m-results.tgz" ] && tar xzf "$LOGS/$m-results.tgz" -C "$LOGS" && cp -R "$LOGS/results/." "$ROOT/results/"
}

if [ "$CONFIRM" != yes ]; then
  for m in $MACHINES; do run_machine "$m"; done
  echo "=== plan only. CONFIRM=yes runs it."
  exit 0
fi
command -v gcloud > /dev/null || { echo "FATAL: gcloud is not installed" >&2; exit 2; }
pids=()
for m in $MACHINES; do ( run_machine "$m" ) & pids+=($!); done
rc=0
for pid in "${pids[@]}"; do wait "$pid" || rc=1; done
echo "=== round $stamp done (rc $rc); logs in $LOGS; results under results/"
exit "$rc"
