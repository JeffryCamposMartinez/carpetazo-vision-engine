# OCR persistente con el motor de Windows (sin instalar nada). Lee rutas de imagen por stdin ("ruta|idioma"), responde una línea JSON por ruta.
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) { $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $null = $t.Wait(-1); $t.Result }
$engines = @{}
foreach ($tag in 'en-US', 'es-ES') { $engines[$tag] = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new($tag)) }
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
while (($line = [Console]::In.ReadLine()) -ne $null) {
  try {
    $parts = $line.Split('|'); $path = $parts[0]; $lang = if ($parts.Count -gt 1 -and $engines.ContainsKey($parts[1])) { $parts[1] } else { 'en-US' }
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($engines[$lang].RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    $lines = @($res.Lines | ForEach-Object { $_.Text })
    ([pscustomobject]@{ ok = $true; lines = $lines } | ConvertTo-Json -Compress -Depth 4)
  } catch { ([pscustomobject]@{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress) }
}
