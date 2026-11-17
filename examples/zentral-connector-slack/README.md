# Example connector: Slack

A complete third-party connector for Zentral, kept deliberately small so it
can be read in one sitting. Use it as a template for your own (Gmail, Jira,
PagerDuty, …). The guide is in `docs/extending/connectors.md`.

```bash
pip install ./examples/zentral-connector-slack
export ZENTRAL_ENABLED_CONNECTORS=webhook,teams,email_graph,slack
export ZENTRAL_OUTBOUND_HOST_ALLOWLIST=graph.microsoft.com,login.microsoftonline.com,slack.com
```

Then add a channel of type **slack** under *Administration → Channels*.
