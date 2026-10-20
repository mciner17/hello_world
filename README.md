# hello_world

Introductory repository.

## portfolio_agent

A deep-research agent for building and rebalancing an investment portfolio:
deterministic allocation, tax-lot and liquidity math, plus Claude-powered
research with live web search. See [portfolio_agent/README.md](portfolio_agent/README.md).

```bash
pip install -r requirements.txt
cp portfolio.example.yaml portfolio.yaml
python -m portfolio_agent.cli status
```
