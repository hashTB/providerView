param(
    [Parameter(Mandatory = $false)]
    [string] $Operation = "echo"
)

# Read all stdin as a single string
$inputJson = [Console]::In.ReadToEnd()

if ([string]::IsNullOrWhiteSpace($inputJson)) {
    Write-Error "No JSON received on stdin."
    exit 1
}

try {
    # Parse JSON into a PowerShell object
    $obj = $inputJson | ConvertFrom-Json
}
catch {
    Write-Error "Failed to parse JSON from stdin: $_"
    exit 1
}

if ($Operation -eq "echo") {
    $response = @{
        message = "Hello from PowerShell"
        name    = $obj.name
        number  = $obj.number
    }

    $response | ConvertTo-Json -Depth 5
    exit 0
}

Write-Error "Unknown operation: $Operation"
exit 1
