#!/usr/bin/env bash
set -e

export VARIANT="v2" # Вариант задания
export SCRIPT_PATH=/docker-entrypoint-initdb.d/
export PGPASSWORD=postgres
psql -f "$SCRIPT_PATH/scripts/db-$VARIANT.sql"
