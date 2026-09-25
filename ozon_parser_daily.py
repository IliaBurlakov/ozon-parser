import os

import pendulum

from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator


PROJECT_DIR = os.environ["OZON_PROJECT_DIR"]
PYTHON = f"{PROJECT_DIR}/.venv/bin/python"


with DAG(
    dag_id="ozon_parser_daily",
    start_date=pendulum.datetime(2026, 9, 25, tz="UTC"),
    schedule="@daily",
    catchup=False,
    max_active_runs=1,
    tags=["ozon"],
) as dag:

    run_parser = BashOperator(
        task_id="run_parser",
        bash_command=f"""
            cd "{PROJECT_DIR}"

            set -a
            source .env
            set +a

            FAILED_FILE="${{RESULTS_FILE%.*}}_failed.txt"

            rm -f "$RESULTS_FILE" "$FAILED_FILE"

            "{PYTHON}" parse_ozon.py
        """,
    )