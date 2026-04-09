# virt-tool

Herramienta para crear recursos de virtualización en `KVM/libvirt` a partir de un archivo YAML.

## Módulos

- `validate`: valida el archivo de configuración
- `plan`: muestra el resumen de red, discos y VMs
- `render-network`: genera el XML de red libvirt
- `build`: crea red, discos y VMs en secuencia
- `create-network`: crea la red libvirt
- `create-disks`: crea los discos de las VMs
- `create-cloud-init`: crea imágenes seed de cloud-init
- `create-vms`: crea las VMs
- `status`: consulta estado actual en libvirt
- `destroy`: destruye recursos definidos

## Requisitos

- `python3`
- `PyYAML`
- `KVM/libvirt` en el host destino

## Preparación del Host

Instalar dependencias base:

```bash
sudo apt-get update
sudo apt-get install -y qemu-system-x86 libvirt-daemon-system virtinst python3-yaml
```

Validar binarios principales:

```bash
command -v python3 virsh virt-install qemu-img
```

Validar `libvirt`:

```bash
systemctl status libvirtd.service --no-pager
virsh list --all
```

## Uso

Validar y revisar el plan:

```bash
sudo python3 -m virt_tool.cli validate --config configs/example-lab.yaml
sudo python3 -m virt_tool.cli plan --config configs/example-lab.yaml
```

Generar y aplicar recursos:

```bash
sudo python3 -m virt_tool.cli render-network --config configs/example-lab.yaml
sudo python3 -m virt_tool.cli build --config configs/example-lab.yaml
sudo python3 -m virt_tool.cli create-network --config configs/example-lab.yaml --apply
sudo python3 -m virt_tool.cli create-disks --config configs/example-lab.yaml --apply
sudo python3 -m virt_tool.cli create-cloud-init --config configs/example-lab.yaml --apply
sudo python3 -m virt_tool.cli create-vms --config configs/example-lab.yaml --apply
```

Consultar y destruir:

```bash
sudo python3 -m virt_tool.cli status --config configs/example-lab.yaml
sudo python3 -m virt_tool.cli destroy --config configs/example-lab.yaml --apply --yes
```

## Operación con virsh

Una vez creadas las VMs, puedes administrarlas directamente con `virsh`.

```bash
sudo virsh list --all
sudo virsh dominfo cp-01
sudo virsh start cp-01
sudo virsh shutdown cp-01
sudo virsh reboot cp-01
sudo virsh destroy cp-01
sudo virsh console cp-01
sudo virsh dumpxml cp-01
sudo virsh net-list --all
sudo virsh net-info k8-lab-net
```

## Nota

- Los comandos mutables hacen `dry-run` por defecto.
- La implementación actual de red soporta `nat`.
- `build --apply` ejecuta `create-network`, `create-disks` y `create-vms`.
- `build --apply` ahora también genera `cloud-init` antes de crear las VMs.
