[CmdletBinding()]
param(
    [ValidateSet('list', 'verify', 'startup', 'search', 'add')]
    [string]$Action = 'verify',
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$Tenant = 'main',
    [string]$Query,
    [ValidateRange(1, 20)]
    [int]$Limit = 5,
    [switch]$FromClipboard,
    [string]$ExpectedTenantId
)

$ErrorActionPreference = 'Stop'
$BoswellRoot = Join-Path $HOME '.boswell'
$ProfileRoot = Join-Path $BoswellRoot 'tenants'
$ApiBase = if ($env:BOSWELL_API_BASE) {
    $env:BOSWELL_API_BASE.TrimEnd('/')
} else {
    'https://delightful-imagination-production-f6a1.up.railway.app'
}

function Get-ProfilePath([string]$Name) {
    Join-Path $ProfileRoot "$Name.key"
}

function Get-ProfileKey([string]$Name) {
    $path = Get-ProfilePath $Name
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Boswell tenant profile '$Name' is not installed."
    }
    $key = (Get-Content -Raw -LiteralPath $path).Trim()
    if (-not $key) {
        throw "Boswell tenant profile '$Name' is empty."
    }
    $key
}

function Invoke-Boswell(
    [string]$Method,
    [string]$Path,
    [string]$Key,
    [hashtable]$Body = $null
) {
    $params = @{
        Method = $Method
        Uri = "$ApiBase$Path"
        Headers = @{'X-API-Key' = $Key; Accept = 'application/json'}
        TimeoutSec = 30
    }
    if ($Body) {
        $params.ContentType = 'application/json'
        $params.Body = $Body | ConvertTo-Json -Depth 20 -Compress
    }
    Invoke-RestMethod @params
}

function Get-TenantIdentity([string]$Name, [string]$Key) {
    $response = Invoke-Boswell GET '/v2/branches' $Key
    $branches = @($response.branches)
    if (-not $branches.Count) {
        throw "Profile '$Name' returned no branches, so its tenant cannot be verified."
    }
    [pscustomobject]@{
        Profile = $Name
        TenantId = $branches[0].tenant_id
        BranchCount = $branches.Count
        HasWren = [bool]($branches.name -contains 'wren')
        Branches = $branches
    }
}

if ($Action -eq 'list') {
    if (-not (Test-Path -LiteralPath $ProfileRoot)) {
        return
    }
    Get-ChildItem -LiteralPath $ProfileRoot -Filter '*.key' |
        Sort-Object BaseName |
        ForEach-Object {
            $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $_.FullName).Hash
            [pscustomobject]@{
                Profile = $_.BaseName
                Fingerprint = $hash.Substring(0, 12).ToLowerInvariant()
            }
        }
    return
}

if ($Action -eq 'add') {
    if (-not $FromClipboard) {
        throw 'Use -FromClipboard so tenant credentials never appear in command arguments.'
    }
    $key = (Get-Clipboard -Raw).Trim()
    if (-not $key) {
        throw 'The clipboard does not contain a tenant key.'
    }
    New-Item -ItemType Directory -Force -Path $ProfileRoot | Out-Null
    Set-Content -LiteralPath (Get-ProfilePath $Tenant) -Value $key -NoNewline -Encoding ascii
    $identity = Get-TenantIdentity $Tenant $key
    if ($ExpectedTenantId -and $identity.TenantId -ne $ExpectedTenantId) {
        Remove-Item -LiteralPath (Get-ProfilePath $Tenant) -Force
        throw "Credential resolved to tenant $($identity.TenantId), not $ExpectedTenantId; profile removed."
    }
    $identity | Select-Object Profile, TenantId, BranchCount, HasWren
    return
}

$profileKey = Get-ProfileKey $Tenant
$identity = Get-TenantIdentity $Tenant $profileKey
if ($ExpectedTenantId -and $identity.TenantId -ne $ExpectedTenantId) {
    throw "Profile '$Tenant' resolved to $($identity.TenantId), not $ExpectedTenantId."
}

Write-Host (
    "BOSWELL TENANT: {0} | {1} | {2} branches | Wren={3}" -f
    $identity.Profile, $identity.TenantId, $identity.BranchCount, $identity.HasWren
)

switch ($Action) {
    'verify' {
        $identity | Select-Object Profile, TenantId, BranchCount, HasWren
    }
    'startup' {
        $startup = Invoke-Boswell GET (
            '/v2/startup?verbosity=warm&agent_id=' +
            [uri]::EscapeDataString("Codex-$Tenant")
        ) $profileKey
        [pscustomobject]@{
            Profile = $Tenant
            TenantId = $identity.TenantId
            Identity = $startup.sacred_manifest.identity
            Mission = $startup.sacred_manifest.mission
            RecentMessages = @($startup.recent_thread | Select-Object -First 8 -ExpandProperty message)
            BootloaderMessages = @($startup.wren_bootloader | Select-Object -First 3 -ExpandProperty message)
        }
    }
    'search' {
        if (-not $Query) {
            throw 'The search action requires -Query.'
        }
        $path = '/v2/search?q={0}&limit={1}&mode=hybrid&depth=surface' -f (
            [uri]::EscapeDataString($Query)), $Limit
        $results = Invoke-Boswell GET $path $profileKey
        @($results.results) | Select-Object commit_hash, branch, message, content_type, created_at
    }
}
