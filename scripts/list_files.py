import os
print(f"Current Directory: {os.getcwd()}")
print("Contents:")
for item in os.listdir('.'):
    print(f"- {item} ({'Dir' if os.path.isdir(item) else 'File'})")
