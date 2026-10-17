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
PY="${PY:-python3}"
# Images for the VMs. Check the GPU family before a round:
#   gcloud compute images list --project deeplearning-platform-release --filter="family~cu12"
CPU_IMAGE_PROJECT="${CPU_IMAGE_PROJECT:-debian-cloud}"
CPU_IMAGE_FAMILY="${CPU_IMAGE_FAMILY:-debian-12}"
GPU_IMAGE_PROJECT="${GPU_IMAGE_PROJECT:-deeplearning-platform-release}"
GPU_IMAGE_FAMILY="${GPU_IMAGE_FAMILY:-common-cu124-debian-11-py310}"

case ",$SPLITS," in
  *,holdout,*) [ -n "$BECAUSE" ] || { echo "REFUSED: SPLITS includes holdout; set BECAUSE to the reason" >&2; exit 2; } ;;
esac

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
LOGS="$ROOT/work/gcp/$stamp"
mkdir -p "$LOGS"

zone_of() { "$PY" -c "import sys; sys.path.insert(0, '.'); from bench import configs; print(configs.machine(sys.argv[1])['zone'])" "$1"; }
disk_of() { "$PY" -c "import sys; sys.path.insert(0, '.'); from bench import configs; print(configs.machine(sys.argv[1]).get('disk_gb', 100))" "$1"; }
gpu_of()  { "$PY" -c "import sys; sys.path.insert(0, '.'); from bench import configs; print(configs.machine(sys.argv[1]).get('gpu') or '')" "$1"; }

# ---- the cost guard, before anything exists ---------------------------------
total=0
for m in $MACHINES; do
  args=(--machine "$m" --configs "$CONFIGS" --sets "$SETS" --splits "$SPLITS" --profile "$PROFILE"
        --reps "$REPS" --budget "$BUDGET_EUR")
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
  local gpu="$1"
  cat <<REMOTE
set -euo pipefail
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
sudo make bench MACHINE=\$(cat /tmp/machine) CONFIGS=$CONFIGS SETS=$SETS SPLITS=$SPLITS \\
  PROFILE=$PROFILE REPS=$REPS RUNNER=docker BECAUSE="$BECAUSE" OPTIONAL=pdfluent-best PY=python3 \\
  || echo "make bench exited non-zero; run.json lists the failed steps"
sudo tar czf ~/results.tgz results work/images.json
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
    echo "  ${scp[*]} <bundle> infra/gcp/vm_setup.sh $name:~/ ; then the remote script below"
    remote_script "$gpu" | sed 's/^/    /'
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
  "${ssh[@]}" --command="echo $m > /tmp/machine"
  remote_script "$gpu" | "${ssh[@]}" --command="bash -s" > "$LOGS/$m-round.log" 2>&1 || echo "round on $m exited non-zero; see $LOGS/$m-round.log" >&2
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
