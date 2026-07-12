def greet(name, verbose=False):
    if verbose:
        print(f"Hello, {name}! Welcome to Simple Math Project.")
    else:
        print(f"Hello, {name}!")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Greet someone.")
    parser.add_argument("name", help="Name to greet")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose greeting")
    args = parser.parse_args()
    greet(args.name, verbose=args.verbose)
