from pybit.unified_trading import HTTP
import inspect

session = HTTP(testnet=True)
methods = [m for m, _ in inspect.getmembers(session, predicate=inspect.ismethod)]
print("Available methods in HTTP object:")
for method in sorted(methods):
    if "time" in method or "server" in method:
        print(f" - {method}")
