# Zentral trial on a VPS

Evaluation with made-up data, on one server, without an identity provider or
a KMS:

```bash
./setup.sh                    # or: ./setup.sh desk.example.com
docker compose run --rm seed  # sample data
```

Needs Docker with Compose, `openssl`, and ports 80/443 open. Sign-in is the
persona picker behind a password that `setup.sh` prints once.

Full guide: [docs/operations/deploy-trial.md](../../docs/operations/deploy-trial.md).
For real data, use [`deploy/compose`](../compose) instead.
