# Microsoft Entra ID artefacts

`app-roles.json` defines Zentral's seven app roles. Apply it to the **API** app
registration:

```bash
az ad app update --id <API_APP_ID> --app-roles @app-roles.json
```

The full walkthrough (two app registrations, scope, role assignment, MCP clients)
is in [docs/operations/identity-entra.md](../../docs/operations/identity-entra.md).
