# Service registration for the checkout API

```json
{"service": "checkout-api", "port": 8080}
```

| Method | Endpoint | Purpose |
|---|---|---|
| GET | /health | liveness probe |
| POST | /orders | create an order |
| GET | /orders/{id} | read an order |
