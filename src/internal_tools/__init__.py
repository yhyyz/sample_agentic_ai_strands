from pathlib import Path
import importlib
import pkgutil
from typing import List, Any

def get_tools() -> List[Any]:
    """Discover and return all tools from this package."""
    tools = []
    package_dir = Path(__file__).parent
    
    # Import all Python modules in the tools directory
    for module_info in pkgutil.iter_modules([str(package_dir)]):
        if module_info.name.startswith('_'):
            continue
            
        try:
            module = importlib.import_module(f".{module_info.name}", package=__package__)
            
            # Find all functions decorated with @tool (DecoratedFunctionTool instances)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                # Check for modern @tool decorated functions (DecoratedFunctionTool)
                if hasattr(attr, 'tool_spec') and hasattr(attr, 'tool_name') and callable(attr):
                    tools.append(attr)
                # Also support legacy TOOL_SPEC pattern for backward compatibility
                elif hasattr(attr, 'TOOL_SPEC') and callable(attr):
                    tools.append(attr)
                    
        except Exception as e:
            print(f"Warning: Failed to load tool module {module_info.name}: {e}")
    
    return tools 