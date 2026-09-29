param([int]$limit = 0, [int]$skip = 0)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing

function get_crop_rectangle($image) {
    $background = $image.GetPixel([int]($image.Width * 0.5), 120).R
    $columns = @(
        for ($column = [int]($image.Width * 0.32); $column -lt $image.Width * 0.744; $column++) {
            $votes = 0
            foreach ($fraction in @(0.40, 0.43, 0.46, 0.49, 0.52, 0.55, 0.58, 0.61, 0.64)) {
                $pixel = $image.GetPixel($column, [int]($image.Height * $fraction))
                if ([Math]::Abs([int]$pixel.R - $background) -gt 4) { $votes++ }
            }
            if ($votes -ge 7) { $column }
        }
    )
    if ($columns.Count -lt $image.Width * 0.25) { throw 'CT-Breite nicht sicher erkannt.' }
    $left = $columns[0]
    $right = $columns[-1]
    $rows = @(
        for ($row = [int]($image.Height * 0.15); $row -lt $image.Height * 0.92; $row++) {
            $votes = 0
            foreach ($sample_index in 1..41) {
                $fraction = $sample_index / 42.0
                $pixel = $image.GetPixel([int]($left + ($right - $left) * $fraction), $row)
                if ([Math]::Abs([int]$pixel.R - $background) -gt 2) { $votes++ }
            }
            if ($votes -ge 18) { $row }
        }
    )
    if ($rows.Count -lt $image.Height * 0.4) { throw 'CT-Hoehe nicht sicher erkannt.' }
    $center_index = [int]($rows.Count / 2)
    $top_index = $center_index
    $bottom_index = $center_index
    while ($top_index -gt 0 -and $rows[$top_index] - $rows[$top_index - 1] -le 12) {
        $top_index--
    }
    while ($bottom_index -lt $rows.Count - 1 -and $rows[$bottom_index + 1] - $rows[$bottom_index] -le 12) {
        $bottom_index++
    }
    $top = $rows[$top_index]
    $bottom = $rows[$bottom_index]
    if ($bottom - $top -lt $image.Height * 0.4) { throw 'CT-Flaeche nicht zusammenhaengend erkannt.' }
    return [System.Drawing.Rectangle]::FromLTRB($left, $top, $right + 1, $bottom + 1)
}

$source_path = Join-Path $PSScriptRoot 'Bilder'
$output_path = Join-Path $PSScriptRoot 'Bilder_Processed_1'
$source_files = @(Get-ChildItem -LiteralPath $source_path -Filter '*.jpg' -File | Sort-Object Name)
if ($skip -gt 0) { $source_files = @($source_files | Select-Object -Skip $skip) }
if ($limit -gt 0) { $source_files = @($source_files | Select-Object -First $limit) }
$source_hashes = @($source_files | Get-FileHash -Algorithm SHA256)
[void][System.IO.Directory]::CreateDirectory($output_path)
$results = @()
foreach ($source_file in $source_files) {
    $image = [System.Drawing.Bitmap]::FromFile($source_file.FullName)
    try {
        $rectangle = get_crop_rectangle $image
        $cropped = $image.Clone($rectangle, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
        try {
            $destination = Join-Path $output_path ($source_file.BaseName + '.png')
            if (Test-Path -LiteralPath $destination) {
                throw "Zieldatei existiert bereits: $destination"
            }
            $cropped.Save($destination, [System.Drawing.Imaging.ImageFormat]::Png)
            $saved = [System.Drawing.Bitmap]::FromFile($destination)
            try {
                if ($saved.Width -ne $rectangle.Width -or $saved.Height -ne $rectangle.Height) {
                    throw 'Gespeicherte Bildmasse stimmen nicht.'
                }
                foreach ($fraction in @(0.1, 0.3, 0.5, 0.7, 0.9)) {
                    $column = [int]($saved.Width * $fraction)
                    $row = [int]($saved.Height * $fraction)
                    $original_pixel = $image.GetPixel($rectangle.X + $column, $rectangle.Y + $row)
                    if ($saved.GetPixel($column, $row).ToArgb() -ne $original_pixel.ToArgb()) {
                        throw 'Pixelpruefung fehlgeschlagen.'
                    }
                }
            } finally { $saved.Dispose() }
            $results += [PSCustomObject]@{
                Name = $source_file.BaseName
                Left = $rectangle.X
                Top = $rectangle.Y
                Width = $rectangle.Width
                Height = $rectangle.Height
            }
        } finally { $cropped.Dispose() }
    } finally { $image.Dispose() }
}
foreach ($source_hash in $source_hashes) {
    if ((Get-FileHash -LiteralPath $source_hash.Path -Algorithm SHA256).Hash -ne $source_hash.Hash) {
        throw 'Eine Originaldatei wurde veraendert.'
    }
}
$results | Format-Table -AutoSize | Out-String -Width 200
Write-Output "$($results.Count) Ausschnitte gespeichert und geprueft. Originaldateien unveraendert."