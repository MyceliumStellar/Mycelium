import argparse
import sys
from mycelium_cli.commands.init import run_init
from mycelium_cli.commands.check import run_check
from mycelium_cli.commands.compile import run_compile
from mycelium_cli.commands.deploy import run_deploy
from mycelium_cli.commands.agent import run_agent

def main():
    parser = argparse.ArgumentParser(prog="mycelium", description="Mycelium Developer Framework CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize a new mycelium agent project")
    init_parser.add_argument("project_name", help="Name of the new project")

    # check
    check_parser = subparsers.add_parser("check", help="Perform AST validation check on a file")
    check_parser.add_argument("file", help="Python file to check")

    # compile
    compile_parser = subparsers.add_parser("compile", help="Compile a Python contract to WASM")
    compile_parser.add_argument("file", help="Python contract file to compile")
    compile_parser.add_argument("-o", "--output", default="build/target.wasm", help="Output path for target WASM")

    # deploy
    deploy_parser = subparsers.add_parser("deploy", help="Deploy a compiled contract to Stellar Network")
    deploy_parser.add_argument("--network", default="testnet", choices=["testnet", "mainnet", "local"], help="Stellar Network target")

    # agent
    agent_parser = subparsers.add_parser("agent", help="Manage mycelium agent runtimes")
    agent_sub = agent_parser.add_subparsers(dest="agent_command", help="Agent action")
    start_parser = agent_sub.add_parser("start", help="Start background agent execution")
    start_parser.add_argument("file", help="Agent script execution file")
    start_parser.add_argument("--contract", required=True, help="On-chain anchor contract hash ID")

    args = parser.parse_args()

    if args.command == "init":
        run_init(args.project_name)
    elif args.command == "check":
        run_check(args.file)
    elif args.command == "compile":
        run_compile(args.file, args.output)
    elif args.command == "deploy":
        run_deploy(args.network)
    elif args.command == "agent":
        if args.agent_command == "start":
            run_agent(args.file, args.contract)
        else:
            agent_parser.print_help()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
