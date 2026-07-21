# Commerce sample

Validate this sample from the repository root:

```bash
python -m omnimock --root samples/commerce validate
python -m omnimock --root samples/commerce run checkout --operation orders.create --payload '{"sku":"SKU-RED","quantity":2}'
```

The sample files are deliberately local and deterministic. No API keys or
network access are required.
