function Read-List([string]$Body) {
 Inspect-Require ($Body.TrimStart().StartsWith('[')) 'READ_RESPONSE_NOT_ARRAY_SAVED_PRIVATELY'
 try {
  # Validate the standalone array before wrapping; command-output enumeration differs on PS5.1.
  $null=ConvertFrom-Json -InputObject $Body
  $envelope=ConvertFrom-Json -InputObject ('{"items":'+$Body+'}')
  Inspect-Require ($envelope.items -is [Array]) 'READ_RESPONSE_JSON_INVALID_SAVED_PRIVATELY'
  $rows=@($envelope.items)
 } catch {throw 'READ_RESPONSE_JSON_INVALID_SAVED_PRIVATELY'}
 Inspect-Require ($rows.Count -le 1000) 'READ_RESPONSE_ROW_LIMIT'
 foreach($row in $rows){Inspect-Require ($null -ne $row -and $row.GetType() -eq [System.Management.Automation.PSCustomObject]) 'READ_RESPONSE_ROW_INVALID'}
 return [pscustomobject]@{rows=$rows;returned_count=$rows.Count;page_limit_reached=($rows.Count -eq 1000);history_completeness_verified=$false}
}
