# Drift VM systemd deployment

This directory turns the current VM-style Drift stack into managed systemd services.

Included services:

- `drift-asyncaiflow.service`
- `drift-backend.service`
- `drift-java-worker@repository.service`
- `drift-java-worker@gpt.service`
- `drift-java-worker@git.service`
- `drift-python-worker@drift_trigger.service`
- `drift-python-worker@drift_web_search.service`
- `drift-python-worker@drift_plan.service`
- `drift-python-worker@drift_code.service`
- `drift-python-worker@drift_review.service`
- `drift-python-worker@drift_test.service`
- `drift-python-worker@drift_deploy.service`
- `drift-python-worker@drift_git_push.service`
- `drift-python-worker@drift_refresh.service`
- Optional: `drift-minecraft.service`

## Install on the VM

Run from the repository copy that exists on the VM:

```bash
cd /path/to/workspace/deploy/systemd
sudo bash ./install-systemd-services.sh
```

The installer:

- auto-detects the AsyncAIFlow and Drift repository roots under the current workspace
- writes `/etc/drift-stack.env` on first run
- installs wrapper scripts into `/opt/drift-stack-systemd/bin`
- installs unit files into `/etc/systemd/system`
- enables and starts the full stack

## Environment file

Review `/etc/drift-stack.env` after first install.

Important keys:

- `STACK_USER`
- `ASYNC_ROOT`
- `DRIFT_ROOT`
- `AIFLOW_JAR`
- `DRIFT_BACKEND_VENV`
- `PYTHON_WORKER_PYTHON`
- `OPENAI_API_KEY`
- `GLM_API_KEY`
- `GLM_BASE_URL_CODING`
- `ENABLE_DRIFT_MINECRAFT`

If you change `/etc/drift-stack.env`, reload and restart:

```bash
sudo systemctl daemon-reload
sudo systemctl restart drift-asyncaiflow.service drift-backend.service
sudo systemctl restart drift-java-worker@repository.service drift-java-worker@gpt.service drift-java-worker@git.service
sudo systemctl restart drift-python-worker@drift_trigger.service drift-python-worker@drift_web_search.service drift-python-worker@drift_plan.service
sudo systemctl restart drift-python-worker@drift_code.service drift-python-worker@drift_review.service drift-python-worker@drift_test.service
sudo systemctl restart drift-python-worker@drift_deploy.service drift-python-worker@drift_git_push.service drift-python-worker@drift_refresh.service
```

## Common commands

```bash
sudo systemctl status drift-asyncaiflow.service
sudo systemctl status drift-backend.service
sudo systemctl status drift-python-worker@drift_code.service
sudo journalctl -u drift-asyncaiflow.service -f
sudo journalctl -u drift-python-worker@drift_refresh.service -f
```

## Notes

- The installer does not build the AsyncAIFlow JAR. Build it first if `target/asyncaiflow-0.1.0-SNAPSHOT.jar` is missing.
- The backend venv must already contain `uvicorn` and backend dependencies.
- `drift_web_search_worker` is included as a stub so difficulty 5 Drift DAGs do not stall.
- Minecraft is left optional because some VM setups need extra memory flags or a different server jar path.

## Runbook

- Game-ready CI/CD and precheck guide: `VM_GAME_READY_CICD_RUNBOOK.md`