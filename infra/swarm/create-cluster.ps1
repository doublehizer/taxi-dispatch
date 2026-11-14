# Создание кластера Docker Swarm из трёх виртуальных машин Multipass (Windows).
# Запуск из PowerShell:  .\create-cluster.ps1
# На Windows Home перед запуском переключите Multipass на VirtualBox:
#   multipass set local.driver=virtualbox

$ErrorActionPreference = "Stop"

$Manager = "manager"
$Workers = @("worker1", "worker2")
$CloudInit = Join-Path $PSScriptRoot "cloud-init.yaml"

foreach ($node in @($Manager) + $Workers) {
    Write-Host "Создание узла $node ..."
    multipass launch 24.04 --name $node --cpus 2 --memory 2G --disk 15G --cloud-init $CloudInit
    multipass exec $node -- cloud-init status --wait
}

$info = multipass info $Manager --format json | ConvertFrom-Json
$ManagerIp = $info.info.$Manager.ipv4[0]
Write-Host "IP управляющего узла: $ManagerIp"

multipass exec $Manager -- sudo docker swarm init --advertise-addr $ManagerIp
$Token = (multipass exec $Manager -- sudo docker swarm join-token -q worker).Trim()

foreach ($node in $Workers) {
    multipass exec $node -- sudo docker swarm join --token $Token "${ManagerIp}:2377"
}

# Узел worker1 выделяется под хранилища данных
multipass exec $Manager -- sudo docker node update --label-add tier=data worker1

multipass exec $Manager -- sudo docker node ls
Write-Host "Кластер готов. Панель будет доступна по адресу http://${ManagerIp}/ после развёртывания."
