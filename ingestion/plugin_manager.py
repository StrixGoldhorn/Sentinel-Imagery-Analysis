import importlib
import inspect
from pathlib import Path
from typing import List, Type
from ingestion.base_plugin import BaseAISScraperPlugin

class PluginManager:
    def __init__(self, plugins_dir: str = "ingestion/plugins"):
        self.plugins_dir = Path(plugins_dir)
        self.plugins = []
        self._load_plugins()

    def _load_plugins(self) -> None:
        if not self.plugins_dir.exists():
            return

        for file in self.plugins_dir.glob("*.py"):
            if file.name != "__init__.py":
                module = importlib.import_module(f"ingestion.plugins.{file.stem}")
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, BaseAISScraperPlugin) and obj is not BaseAISScraperPlugin:
                        self.plugins.append(obj)

    def get_plugins(self) -> List[Type[BaseAISScraperPlugin]]:
        return self.plugins