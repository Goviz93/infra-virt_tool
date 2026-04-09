from __future__ import annotations

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    pass


@dataclass
class ToolConfig:
    name: str
    description: str = ""


@dataclass
class NetworkConfig:
    name: str
    mode: str
    bridge: str
    subnet: str
    gateway: str
    dns: list[str] = field(default_factory=list)
    domain: str = "lab.internal"
    dhcp_range_start: str = ""
    dhcp_range_end: str = ""
    autostart: bool = True

    def validate(self) -> None:
        if self.mode != "nat":
            raise ConfigError(
                f"Unsupported network mode: {self.mode}. Current implementation supports only 'nat'"
            )

        network = IPv4Network(self.subnet, strict=False)
        gateway = IPv4Address(self.gateway)
        if gateway not in network:
            raise ConfigError(
                f"Gateway {self.gateway} is not inside subnet {self.subnet}"
            )

        if self.dhcp_range_start:
            start = IPv4Address(self.dhcp_range_start)
            if start not in network:
                raise ConfigError(
                    f"DHCP range start {self.dhcp_range_start} is not inside subnet {self.subnet}"
                )

        if self.dhcp_range_end:
            end = IPv4Address(self.dhcp_range_end)
            if end not in network:
                raise ConfigError(
                    f"DHCP range end {self.dhcp_range_end} is not inside subnet {self.subnet}"
                )


@dataclass
class DefaultsConfig:
    libvirt_uri: str = "qemu:///system"
    storage_dir: str = "/var/lib/libvirt/images/virt-tool"
    os_variant: str = "ubuntu24.04"
    disk_format: str = "qcow2"
    graphics: str = "none"
    network_name: str = ""
    base_image: str = ""
    cpu_mode: str = "host-passthrough"
    cloud_init_dir: str = "/var/lib/libvirt/images/virt-tool/cloud-init"
    default_user: str = "ubuntu"
    ssh_public_key: str = ""


@dataclass
class VMNetworkConfig:
    ipv4: str
    gateway4: str
    dns4: list[str] = field(default_factory=list)

    def validate(self, subnet: str) -> None:
        network = IPv4Network(subnet, strict=False)
        ipv4 = IPv4Address(self.ipv4)
        gateway = IPv4Address(self.gateway4)

        if ipv4 not in network:
            raise ConfigError(f"VM IP {self.ipv4} is not inside subnet {subnet}")
        if gateway not in network:
            raise ConfigError(
                f"VM gateway {self.gateway4} is not inside subnet {subnet}"
            )


@dataclass
class VMConfig:
    name: str
    hostname: str
    vcpu: int
    memory_mb: int
    os_disk_gb: int
    data_disks_gb: list[int] = field(default_factory=list)
    network: VMNetworkConfig | None = None
    network_name: str = ""
    mac: str = ""
    tags: list[str] = field(default_factory=list)
    autostart: bool = True

    def validate(self, subnet: str | None) -> None:
        if self.vcpu <= 0:
            raise ConfigError(f"{self.name}: vcpu must be > 0")
        if self.memory_mb <= 0:
            raise ConfigError(f"{self.name}: memory_mb must be > 0")
        if self.os_disk_gb <= 0:
            raise ConfigError(f"{self.name}: os_disk_gb must be > 0")
        if any(size <= 0 for size in self.data_disks_gb):
            raise ConfigError(f"{self.name}: data_disks_gb values must be > 0")
        if subnet and self.network:
            self.network.validate(subnet)


@dataclass
class Config:
    path: Path
    tool: ToolConfig
    network: NetworkConfig | None
    defaults: DefaultsConfig
    vms: list[VMConfig]

    @property
    def runtime_dir(self) -> Path:
        return self.path.parent.parent / ".virt-tool"

    @property
    def network_xml_path(self) -> Path:
        return self.runtime_dir / f"{self.tool.name}-network.xml"

    def validate(self) -> None:
        if not self.vms:
            raise ConfigError("At least one VM must be defined")

        subnet = None
        if self.network:
            self.network.validate()
            subnet = self.network.subnet

        seen_names: set[str] = set()
        seen_ips: set[str] = set()
        seen_macs: set[str] = set()

        for vm in self.vms:
            if vm.name in seen_names:
                raise ConfigError(f"Duplicate VM name: {vm.name}")
            seen_names.add(vm.name)
            vm.validate(subnet)

            if vm.network:
                if vm.network.ipv4 in seen_ips:
                    raise ConfigError(f"Duplicate VM IP: {vm.network.ipv4}")
                seen_ips.add(vm.network.ipv4)

            if vm.mac:
                if vm.mac in seen_macs:
                    raise ConfigError(f"Duplicate VM MAC: {vm.mac}")
                seen_macs.add(vm.mac)


def _require_mapping(data: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ConfigError(f"{field_name} must be a mapping")
    return data


def _require_list(data: Any, field_name: str) -> list[Any]:
    if not isinstance(data, list):
        raise ConfigError(f"{field_name} must be a list")
    return data


def _get_str(data: dict[str, Any], key: str, default: str | None = None) -> str:
    if key not in data:
        if default is not None:
            return default
        raise ConfigError(f"Missing required field: {key}")
    value = data[key]
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string")
    return value


def _get_int(data: dict[str, Any], key: str) -> int:
    if key not in data or not isinstance(data[key], int):
        raise ConfigError(f"{key} must be an integer")
    return data[key]


def _get_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean")
    return value


def _list_of_str(data: dict[str, Any], key: str) -> list[str]:
    items = _require_list(data.get(key, []), key)
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise ConfigError(f"{key} must contain only strings")
        result.append(item)
    return result


def _list_of_int(data: dict[str, Any], key: str) -> list[int]:
    items = _require_list(data.get(key, []), key)
    result: list[int] = []
    for item in items:
        if not isinstance(item, int):
            raise ConfigError(f"{key} must contain only integers")
        result.append(item)
    return result


def load_config(path: str | Path) -> Config:
    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ConfigError(
            "PyYAML is required. Install it with: pip install pyyaml"
        ) from exc

    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text())
    root = _require_mapping(raw, "root")

    tool_raw = _require_mapping(root.get("tool", {}), "tool")
    tool = ToolConfig(
        name=_get_str(tool_raw, "name"),
        description=_get_str(tool_raw, "description", default=""),
    )

    network = None
    if "network" in root and root["network"] is not None:
        network_raw = _require_mapping(root["network"], "network")
        network = NetworkConfig(
            name=_get_str(network_raw, "name"),
            mode=_get_str(network_raw, "mode"),
            bridge=_get_str(network_raw, "bridge"),
            subnet=_get_str(network_raw, "subnet"),
            gateway=_get_str(network_raw, "gateway"),
            dns=_list_of_str(network_raw, "dns"),
            domain=_get_str(network_raw, "domain", default="lab.internal"),
            dhcp_range_start=_get_str(
                network_raw, "dhcp_range_start", default=""
            ),
            dhcp_range_end=_get_str(network_raw, "dhcp_range_end", default=""),
            autostart=_get_bool(network_raw, "autostart", default=True),
        )

    defaults_raw = _require_mapping(root.get("defaults", {}), "defaults")
    defaults = DefaultsConfig(
        libvirt_uri=_get_str(
            defaults_raw, "libvirt_uri", default="qemu:///system"
        ),
        storage_dir=_get_str(
            defaults_raw,
            "storage_dir",
            default="/var/lib/libvirt/images/virt-tool",
        ),
        os_variant=_get_str(defaults_raw, "os_variant", default="ubuntu24.04"),
        disk_format=_get_str(defaults_raw, "disk_format", default="qcow2"),
        graphics=_get_str(defaults_raw, "graphics", default="none"),
        network_name=_get_str(defaults_raw, "network_name", default=""),
        base_image=_get_str(defaults_raw, "base_image", default=""),
        cpu_mode=_get_str(defaults_raw, "cpu_mode", default="host-passthrough"),
        cloud_init_dir=_get_str(
            defaults_raw,
            "cloud_init_dir",
            default="/var/lib/libvirt/images/virt-tool/cloud-init",
        ),
        default_user=_get_str(defaults_raw, "default_user", default="ubuntu"),
        ssh_public_key=_get_str(defaults_raw, "ssh_public_key", default=""),
    )

    vms_raw = _require_list(root.get("vms", []), "vms")
    vms: list[VMConfig] = []
    for item in vms_raw:
        vm_raw = _require_mapping(item, "vm")
        vm_network = None
        if "network" in vm_raw and vm_raw["network"] is not None:
            vm_network_raw = _require_mapping(
                vm_raw["network"], f"{vm_raw.get('name', 'vm')}.network"
            )
            vm_network = VMNetworkConfig(
                ipv4=_get_str(vm_network_raw, "ipv4"),
                gateway4=_get_str(vm_network_raw, "gateway4"),
                dns4=_list_of_str(vm_network_raw, "dns4"),
            )
        vms.append(
            VMConfig(
                name=_get_str(vm_raw, "name"),
                hostname=_get_str(vm_raw, "hostname"),
                vcpu=_get_int(vm_raw, "vcpu"),
                memory_mb=_get_int(vm_raw, "memory_mb"),
                os_disk_gb=_get_int(vm_raw, "os_disk_gb"),
                data_disks_gb=_list_of_int(vm_raw, "data_disks_gb"),
                network=vm_network,
                network_name=_get_str(vm_raw, "network_name", default=""),
                mac=_get_str(vm_raw, "mac", default=""),
                tags=_list_of_str(vm_raw, "tags"),
                autostart=_get_bool(vm_raw, "autostart", default=True),
            )
        )

    config = Config(
        path=config_path,
        tool=tool,
        network=network,
        defaults=defaults,
        vms=vms,
    )
    config.validate()
    return config
