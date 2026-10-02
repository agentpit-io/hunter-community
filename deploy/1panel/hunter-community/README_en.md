## Introduction

HunterCode Community Edition is a self-hosted AI investment research assistant covering A-share, Hong Kong and US markets. Market data, stock screening, backtesting and natural-language chat live in one interface, and your watchlists, positions and conversations never leave your own server.

The first visit opens a five-step setup wizard: enter the setup token, run the environment check, pick an LLM provider, paste and test an API key, and choose a data source. No configuration file editing is required.

## Features

- **Natural-language research chat** backed by market data, filings and news tools.
- **Magic screener** that turns one sentence into a stock screen across A-share, HK and US markets.
- **Watchlist and position tracking** with events and signals delivered into the chat.
- **Built-in LLM adapter** for any OpenAI-compatible endpoint; model settings are stored in the database and take effect without restarting containers.
- **Extensible SKILLs**, shipped with the image and editable from the web UI.
- **Local-first data**: conversations, watchlists, positions and imported data packages all stay in the host directories of this app.

## Notes

- The setup token is generated during installation and can be read at any time under App - Parameters.
- The app runs in multi-user mode; the first account you register becomes the administrator.
- Only the web service publishes a host port (3180 by default). Put a reverse proxy with HTTPS in front of it.
- Minimum 2 vCPU / 3 GB RAM, 2 vCPU / 6 GB recommended, 10 GB or more free disk.
