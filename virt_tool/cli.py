from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

from .config import Config, ConfigError, VMConfig, load_config


class CommandError(Exception):
    pass


def run(cmd: list[str], apply: bool) -> None:
    print(shlex.join(cmd))
    if apply:
        subprocess.run(cmd, check=True)


def capture(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout


def ensure_runtime_dir(config: Config) -> None:
    config.runtime_dir.mkdir(parents=True, exist_ok=True)


def vm_os_disk_path(config: Config, vm: VMConfig) -> Path:
    suffix = "qcow2" if config.defaults.disk_format == "qcow2" else "img"
    return Path(config.defaults.storage_dir) / f"{vm.name}-os.{suffix}"


def vm_data_disk_paths(config: Config, vm: VMConfig) -> list[Path]:
    suffix = "qcow2" if config.defaults.disk_format == "qcow2" else "img"
    return [
        Path(config.defaults.storage_dir) / f"{vm.name}-data{idx + 1}.{suffix}"
        for idx, _ in enumerate(vm.data_disks_gb)
    ]


def vm_cloud_init_dir(config: Config, vm: VMConfig) -> Path:
    return Path(config.defaults.cloud_init_dir) / vm.name


def vm_seed_iso_path(config: Config, vm: VMConfig) -> Path:
    return vm_cloud_init_dir(config, vm) / f"{vm.name}-seed.iso"


def vm_user_data_path(config: Config, vm: VMConfig) -> Path:
    return vm_cloud_init_dir(config, vm) / "user-data"


def vm_meta_data_path(config: Config, vm: VMConfig) -> Path:
    return vm_cloud_init_dir(config, vm) / "meta-data"


def render_network_xml(config: Config) -> str:
    if not config.network:
        raise CommandError("Network definition is required for this command")

    hosts = []
    for vm in config.vms:
        if vm.mac and vm.network:
            hosts.append(
                f"      <host mac='{vm.mac}' name='{vm.name}' ip='{vm.network.ipv4}'/>"
            )

    host_block = "\n".join(hosts)
    dns_block = (
        f"  <domain name='{config.network.domain}'/>\n" if config.network.domain else ""
    )

    range_block = ""
    if config.network.dhcp_range_start and config.network.dhcp_range_end:
        range_block = (
            f"      <range start='{config.network.dhcp_range_start}' "
            f"end='{config.network.dhcp_range_end}'/>"
        )

    if range_block and host_block:
        dhcp_block = f"{range_block}\n{host_block}"
    else:
        dhcp_block = range_block or host_block

    if dhcp_block:
        dhcp_block = f"    <dhcp>\n{dhcp_block}\n    </dhcp>\n"

    forward_block = (
        "<forward mode='nat'/>"
        if config.network.mode == "nat"
        else "<forward mode='bridge'/>"
    )

    return (
        "<network>\n"
        f"  <name>{config.network.name}</name>\n"
        f"  <bridge name='{config.network.bridge}' stp='on' delay='0'/>\n"
        f"  {forward_block}\n"
        f"{dns_block}"
        f"  <ip address='{config.network.gateway}' netmask='255.255.255.0'>\n"
        f"{dhcp_block}"
        "  </ip>\n"
        "</network>\n"
    )


def virt_install_command(config: Config, vm: VMConfig) -> list[str]:
    network_name = vm.network_name or config.defaults.network_name or (
        config.network.name if config.network else ""
    )
    if not network_name:
        raise CommandError(f"{vm.name}: network_name is not defined")

    cmd = [
        "virt-install",
        "--connect",
        config.defaults.libvirt_uri,
        "--name",
        vm.name,
        "--memory",
        str(vm.memory_mb),
        "--vcpus",
        str(vm.vcpu),
        "--cpu",
        config.defaults.cpu_mode,
        "--os-variant",
        config.defaults.os_variant,
        "--import",
        "--graphics",
        config.defaults.graphics,
        "--noautoconsole",
    ]

    os_disk = vm_os_disk_path(config, vm)
    cmd.extend(
        [
            "--disk",
            f"path={os_disk},format={config.defaults.disk_format},bus=virtio",
        ]
    )

    for disk_path in vm_data_disk_paths(config, vm):
        cmd.extend(
            [
                "--disk",
                f"path={disk_path},format={config.defaults.disk_format},bus=virtio",
            ]
        )

    seed_iso = vm_seed_iso_path(config, vm)
    cmd.extend(
        [
            "--disk",
            f"path={seed_iso},device=cdrom",
        ]
    )

    network_arg = f"network={network_name},model=virtio"
    if vm.mac:
        network_arg += f",mac={vm.mac}"
    cmd.extend(["--network", network_arg])

    if vm.autostart:
        cmd.append("--autostart")

    return cmd


def render_user_data(config: Config, vm: VMConfig) -> str:
    ssh_key_block = ""
    if config.defaults.ssh_public_key:
        ssh_key_block = (
            "    ssh_authorized_keys:\n"
            f"      - {config.defaults.ssh_public_key}\n"
        )

    return (
        "#cloud-config\n"
        f"hostname: {vm.hostname}\n"
        "preserve_hostname: false\n"
        "users:\n"
        "  - default\n"
        f"  - name: {config.defaults.default_user}\n"
        "    sudo: ALL=(ALL) NOPASSWD:ALL\n"
        "    groups: sudo\n"
        "    shell: /bin/bash\n"
        f"{ssh_key_block}"
        "ssh_pwauth: false\n"
        "disable_root: true\n"
    )


def render_meta_data(config: Config, vm: VMConfig) -> str:
    return (
        f"instance-id: {config.tool.name}-{vm.name}\n"
        f"local-hostname: {vm.hostname}\n"
    )


def cmd_validate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    print(f"Config valid: {config.path}")
    print(f"Tool name: {config.tool.name}")
    print(f"VM count: {len(config.vms)}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    total_vcpu = sum(vm.vcpu for vm in config.vms)
    total_memory_mb = sum(vm.memory_mb for vm in config.vms)
    total_os_disk_gb = sum(vm.os_disk_gb for vm in config.vms)
    total_data_disk_gb = sum(sum(vm.data_disks_gb) for vm in config.vms)

    print(f"Plan for: {config.tool.name}")
    if config.tool.description:
        print(f"Description: {config.tool.description}")
    print()

    if config.network:
        print("Network:")
        print(f"  name: {config.network.name}")
        print(f"  mode: {config.network.mode}")
        print(f"  bridge: {config.network.bridge}")
        print(f"  subnet: {config.network.subnet}")
        print(f"  gateway: {config.network.gateway}")
        print(f"  autostart: {config.network.autostart}")
    else:
        print("Network: not defined")
    print()

    print("Defaults:")
    print(f"  libvirt_uri: {config.defaults.libvirt_uri}")
    print(f"  storage_dir: {config.defaults.storage_dir}")
    print(f"  base_image: {config.defaults.base_image or '(none)'}")
    print(f"  os_variant: {config.defaults.os_variant}")
    print(f"  disk_format: {config.defaults.disk_format}")
    print(f"  graphics: {config.defaults.graphics}")
    print()

    print("VMs:")
    for vm in config.vms:
        print(
            f"  - {vm.name}: {vm.vcpu} vCPU, {vm.memory_mb} MB RAM, "
            f"{vm.os_disk_gb} GB OS disk, data={vm.data_disks_gb or []}"
        )
        print(f"    os disk path: {vm_os_disk_path(config, vm)}")
        if vm.data_disks_gb:
            print(f"    data disk paths: {', '.join(map(str, vm_data_disk_paths(config, vm)))}")
        if vm.network:
            print(f"    ip: {vm.network.ipv4}")
        if vm.mac:
            print(f"    mac: {vm.mac}")
        if vm.tags:
            print(f"    tags: {', '.join(vm.tags)}")
    print()

    print("Totals:")
    print(f"  vcpu: {total_vcpu}")
    print(f"  memory_mb: {total_memory_mb}")
    print(f"  os_disk_gb: {total_os_disk_gb}")
    print(f"  data_disk_gb: {total_data_disk_gb}")
    return 0


def cmd_render_network(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    ensure_runtime_dir(config)
    xml = render_network_xml(config)
    config.network_xml_path.write_text(xml)
    print(f"Rendered network XML: {config.network_xml_path}")
    print()
    print(xml, end="")
    return 0


def cmd_create_network(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    ensure_runtime_dir(config)
    xml = render_network_xml(config)
    config.network_xml_path.write_text(xml)

    print(f"Network XML: {config.network_xml_path}")
    run(
        [
            "virsh",
            "--connect",
            config.defaults.libvirt_uri,
            "net-define",
            str(config.network_xml_path),
        ],
        apply=args.apply,
    )
    run(
        ["virsh", "--connect", config.defaults.libvirt_uri, "net-start", config.network.name],
        apply=args.apply,
    )
    if config.network and config.network.autostart:
        run(
            [
                "virsh",
                "--connect",
                config.defaults.libvirt_uri,
                "net-autostart",
                config.network.name,
            ],
            apply=args.apply,
        )
    return 0


def create_disk_commands(config: Config, vm: VMConfig) -> list[list[str]]:
    commands: list[list[str]] = []
    os_disk = vm_os_disk_path(config, vm)
    if config.defaults.base_image:
        commands.append(
            [
                "qemu-img",
                "create",
                "-f",
                config.defaults.disk_format,
                "-F",
                "qcow2",
                "-b",
                config.defaults.base_image,
                str(os_disk),
                f"{vm.os_disk_gb}G",
            ]
        )
    else:
        commands.append(
            [
                "qemu-img",
                "create",
                "-f",
                config.defaults.disk_format,
                str(os_disk),
                f"{vm.os_disk_gb}G",
            ]
        )

    for idx, size in enumerate(vm.data_disks_gb, start=1):
        data_disk = Path(config.defaults.storage_dir) / (
            f"{vm.name}-data{idx}.{config.defaults.disk_format}"
        )
        commands.append(
            [
                "qemu-img",
                "create",
                "-f",
                config.defaults.disk_format,
                str(data_disk),
                f"{size}G",
            ]
        )

    return commands


def cmd_create_disks(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    storage_dir = Path(config.defaults.storage_dir)
    print(f"Storage directory: {storage_dir}")
    if args.apply:
        storage_dir.mkdir(parents=True, exist_ok=True)

    for vm in config.vms:
        print()
        print(f"Disks for {vm.name}:")
        for cmd in create_disk_commands(config, vm):
            run(cmd, apply=args.apply)
    return 0


def cmd_create_cloud_init(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    if not config.defaults.ssh_public_key:
        raise CommandError(
            "defaults.ssh_public_key is required to generate cloud-init access"
        )

    for vm in config.vms:
        ci_dir = vm_cloud_init_dir(config, vm)
        user_data = vm_user_data_path(config, vm)
        meta_data = vm_meta_data_path(config, vm)
        seed_iso = vm_seed_iso_path(config, vm)

        print()
        print(f"Cloud-init for {vm.name}:")
        print(f"  dir: {ci_dir}")
        print(f"  seed: {seed_iso}")

        if args.apply:
            ci_dir.mkdir(parents=True, exist_ok=True)
            user_data.write_text(render_user_data(config, vm))
            meta_data.write_text(render_meta_data(config, vm))

        run(
            [
                "cloud-localds",
                str(seed_iso),
                str(user_data),
                str(meta_data),
            ],
            apply=args.apply,
        )
    return 0


def cmd_create_vms(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    for vm in config.vms:
        print()
        print(f"Create VM: {vm.name}")
        run(virt_install_command(config, vm), apply=args.apply)
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    print("== Build: network ==")
    build_args = argparse.Namespace(config=args.config, apply=args.apply)
    cmd_create_network(build_args)

    print()
    print("== Build: disks ==")
    cmd_create_disks(build_args)

    print()
    print("== Build: cloud-init ==")
    cmd_create_cloud_init(build_args)

    print()
    print("== Build: vms ==")
    cmd_create_vms(build_args)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = load_config(args.config)

    if config.network:
        print(f"Network status: {config.network.name}")
        print(
            capture(
                [
                    "virsh",
                    "--connect",
                    config.defaults.libvirt_uri,
                    "net-info",
                    config.network.name,
                ]
            ),
            end="",
        )
        print()

    print("VM status:")
    for vm in config.vms:
        print(f"[{vm.name}]")
        try:
            print(
                capture(
                    [
                        "virsh",
                        "--connect",
                        config.defaults.libvirt_uri,
                        "dominfo",
                        vm.name,
                    ]
                ),
                end="",
            )
        except subprocess.CalledProcessError:
            print("not defined")
        print()
    return 0


def cmd_destroy(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    if args.apply and not args.yes:
        raise CommandError("Destroy requires --yes together with --apply")

    for vm in config.vms:
        print()
        print(f"Destroy VM: {vm.name}")
        run(
            ["virsh", "--connect", config.defaults.libvirt_uri, "destroy", vm.name],
            apply=args.apply,
        )
        undefine_cmd = [
            "virsh",
            "--connect",
            config.defaults.libvirt_uri,
            "undefine",
            vm.name,
        ]
        if args.delete_storage:
            undefine_cmd.append("--remove-all-storage")
        run(undefine_cmd, apply=args.apply)

    if config.network:
        print()
        print(f"Destroy network: {config.network.name}")
        run(
            ["virsh", "--connect", config.defaults.libvirt_uri, "net-destroy", config.network.name],
            apply=args.apply,
        )
        run(
            ["virsh", "--connect", config.defaults.libvirt_uri, "net-undefine", config.network.name],
            apply=args.apply,
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="virt-tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate config")
    validate_parser.add_argument("--config", required=True, help="Path to YAML config")
    validate_parser.set_defaults(func=cmd_validate)

    plan_parser = subparsers.add_parser("plan", help="Show execution plan")
    plan_parser.add_argument("--config", required=True, help="Path to YAML config")
    plan_parser.set_defaults(func=cmd_plan)

    render_network_parser = subparsers.add_parser(
        "render-network", help="Render libvirt network XML"
    )
    render_network_parser.add_argument(
        "--config", required=True, help="Path to YAML config"
    )
    render_network_parser.set_defaults(func=cmd_render_network)

    create_network_parser = subparsers.add_parser(
        "create-network", help="Define and start the libvirt network"
    )
    create_network_parser.add_argument(
        "--config", required=True, help="Path to YAML config"
    )
    create_network_parser.add_argument(
        "--apply", action="store_true", help="Apply changes instead of dry-run"
    )
    create_network_parser.set_defaults(func=cmd_create_network)

    create_disks_parser = subparsers.add_parser(
        "create-disks", help="Create VM disk images"
    )
    create_disks_parser.add_argument(
        "--config", required=True, help="Path to YAML config"
    )
    create_disks_parser.add_argument(
        "--apply", action="store_true", help="Apply changes instead of dry-run"
    )
    create_disks_parser.set_defaults(func=cmd_create_disks)

    create_cloud_init_parser = subparsers.add_parser(
        "create-cloud-init", help="Create cloud-init seed images"
    )
    create_cloud_init_parser.add_argument(
        "--config", required=True, help="Path to YAML config"
    )
    create_cloud_init_parser.add_argument(
        "--apply", action="store_true", help="Apply changes instead of dry-run"
    )
    create_cloud_init_parser.set_defaults(func=cmd_create_cloud_init)

    create_vms_parser = subparsers.add_parser(
        "create-vms", help="Create libvirt VMs"
    )
    create_vms_parser.add_argument(
        "--config", required=True, help="Path to YAML config"
    )
    create_vms_parser.add_argument(
        "--apply", action="store_true", help="Apply changes instead of dry-run"
    )
    create_vms_parser.set_defaults(func=cmd_create_vms)

    build_parser = subparsers.add_parser(
        "build", help="Create all resources defined in the config"
    )
    build_parser.add_argument(
        "--config", required=True, help="Path to YAML config"
    )
    build_parser.add_argument(
        "--apply", action="store_true", help="Apply changes instead of dry-run"
    )
    build_parser.set_defaults(func=cmd_build)

    status_parser = subparsers.add_parser(
        "status", help="Show libvirt status for configured resources"
    )
    status_parser.add_argument("--config", required=True, help="Path to YAML config")
    status_parser.set_defaults(func=cmd_status)

    destroy_parser = subparsers.add_parser(
        "destroy", help="Destroy configured resources"
    )
    destroy_parser.add_argument("--config", required=True, help="Path to YAML config")
    destroy_parser.add_argument(
        "--apply", action="store_true", help="Apply changes instead of dry-run"
    )
    destroy_parser.add_argument(
        "--yes", action="store_true", help="Confirm destructive action"
    )
    destroy_parser.add_argument(
        "--delete-storage",
        action="store_true",
        help="Also remove libvirt-managed disk storage on undefine",
    )
    destroy_parser.set_defaults(func=cmd_destroy)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except CommandError as exc:
        print(f"Command error: {exc}", file=sys.stderr)
        return 3
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}: {exc.cmd}", file=sys.stderr)
        return exc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
