from __future__ import annotations

import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from packaging.requirements import Requirement
import pytest

from scripts import healthcheck
from src.agents.supervisor import Intent, QueryAnalysis, SupervisorAgent


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("method, intent", [
    ("_execute_stock_analysis", Intent.STOCK_ANALYSIS),
    ("_execute_quick_analysis", Intent.QUICK_ANALYSIS),
])
@pytest.mark.parametrize("stocks, expected_status", [([], "error"), ([{"name": "Example", "code": "005930"}], "removed")])
def test_retired_supervisor_stock_routes_preserve_responses_without_loading_agents(method, intent, stocks, expected_status):
    supervisor = SupervisorAgent.__new__(SupervisorAgent)
    result = getattr(supervisor, method)(QueryAnalysis(original_query="query", intent=intent, stocks=stocks))
    assert result["status"] == expected_status
    assert result["message"]
    assert "is_langgraph_available" not in inspect.getsource(getattr(SupervisorAgent, method))


@pytest.mark.parametrize("theme, expected", [("AI", "AI"), (None, "query")])
def test_retired_supervisor_theme_route_preserves_response(theme, expected):
    supervisor = SupervisorAgent.__new__(SupervisorAgent)
    result = supervisor._execute_theme_screening(QueryAnalysis(
        original_query="query", intent=Intent.THEME_SCREENING, theme=theme,
    ))
    assert result["status"] == "removed"
    assert result["theme"] == expected


def test_healthcheck_checks_active_transport_dependencies(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(healthcheck, "load_project_env", lambda: None)
    monkeypatch.setattr(healthcheck, "get_settings", lambda: SimpleNamespace(data_dir=tmp_path, project_root=ROOT))
    monkeypatch.setattr(healthcheck, "get_env_status", lambda: SimpleNamespace(loaded=False, path=None, message="test"))
    monkeypatch.setattr(healthcheck, "EvidenceRetriever", lambda **_: SimpleNamespace(describe_data_state=lambda: {}))
    monkeypatch.setattr(healthcheck, "_module_status", lambda name: {"module": name, "ok": True})
    assert healthcheck.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert {row["module"] for row in result["dependencies"]} == {
        "dotenv", "langchain_core", "langchain_openai", "openai", "rank_bm25",
    }


def test_requirements_keep_active_providers_and_remove_retired_stacks():
    requirements = {
        Requirement(line).name
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {"langchain-core", "langchain-openai", "openai", "langchain-ollama",
            "exchange-calendars", "finance-datareader", "selenium", "playwright"} <= requirements
    assert requirements.isdisjoint({
        "langchain-community", "langchain-google-genai", "langchain-anthropic", "langgraph",
        "chromadb", "tokenizers", "sentence-transformers", "langchain-huggingface",
        "PyMuPDF", "Pillow", "duckduckgo-search", "tavily-python",
    })


def test_active_runtime_imports_without_retired_packages():
    program = '''
import importlib.abc
import inspect
import socket
import sys

def deny_network(*args, **kwargs):
    raise AssertionError("No network is allowed in the dependency smoke test")

socket.create_connection = deny_network
socket.getaddrinfo = deny_network
socket.socket.connect = deny_network
socket.socket.connect_ex = deny_network

class BlockRetiredPackages(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {
            "langchain_community", "langchain_google_genai", "langchain_anthropic", "langgraph",
            "chromadb", "tokenizers", "sentence_transformers", "langchain_huggingface",
            "fitz", "pymupdf", "PIL", "duckduckgo_search", "tavily",
        }:
            raise ImportError("Retired dependency is unavailable: " + fullname)

sys.meta_path.insert(0, BlockRetiredPackages())
from src.agents import AnalystAgent, QuantAgent, ChartistAgent, RiskManagerAgent, SupervisorAgent
from src.utils.luna_chat import LunaChatOpenAI
from langchain_ollama import ChatOllama
from src.runner.shared_analysis import SharedAnalysisService
from src.evidence.retriever import EvidenceRetriever
import backtesting

for item in (AnalystAgent, QuantAgent, ChartistAgent, RiskManagerAgent, SupervisorAgent,
             LunaChatOpenAI, ChatOllama, SharedAnalysisService, EvidenceRetriever):
    assert inspect.isclass(item), item
'''
    result = subprocess.run(
        [sys.executable, "-c", program], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
