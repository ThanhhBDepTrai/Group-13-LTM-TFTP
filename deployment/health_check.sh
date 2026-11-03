#!/usr/bin/env bash
curl -fsS "http://${1:-127.0.0.1:6971}/api/health"
