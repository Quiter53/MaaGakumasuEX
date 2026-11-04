using Avalonia.Controls;
using SharpCompress.Archives;
using SharpCompress.Archives.Tar;
using SharpCompress.Common;
using SharpCompress.Readers;
using System.Linq;
using System.Threading.Tasks;

namespace MFAAvalonia.Helper;

using System;
using System.IO;

public class UniversalExtractor
{
    private static string GetExtractionPath(string destinationDirectory, IEntry entry)
    {
        if (string.IsNullOrWhiteSpace(entry.Key) || !string.IsNullOrEmpty(entry.LinkTarget))
            throw new InvalidDataException("压缩包包含无效路径或链接条目");

        var entryName = entry.Key.Replace('\\', Path.DirectorySeparatorChar).Replace('/', Path.DirectorySeparatorChar);
        if (Path.IsPathRooted(entryName))
            throw new InvalidDataException("压缩包包含绝对路径");

        var root = Path.GetFullPath(destinationDirectory).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        var outputPath = Path.GetFullPath(Path.Combine(root, entryName));
        var comparison = OperatingSystem.IsWindows() ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal;
        if (!outputPath.StartsWith(root, comparison))
            throw new InvalidDataException("压缩包路径超出解压目录");

        if (OperatingSystem.IsWindows() && entryName.Split(Path.DirectorySeparatorChar).Any(
                part => part.Contains(':') || part.EndsWith(' ') || (part.EndsWith('.') && part != "." && part != "..")))
            throw new InvalidDataException("压缩包包含无效的 Windows 文件路径");

        // A previously extracted link must not redirect a later entry outside the staging directory.
        for (var current = outputPath; current != null && (current + Path.DirectorySeparatorChar).StartsWith(root, comparison);
             current = Path.GetDirectoryName(current))
        {
            try
            {
                if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("解压路径包含文件系统链接");
            }
            catch (FileNotFoundException) { }
            catch (DirectoryNotFoundException) { }
        }
        return outputPath;
    }

    public static bool Extract(string compressedFilePath, string destinationDirectory)
    {
        // 创建目标目录（如果不存在）
        Directory.CreateDirectory(destinationDirectory);

        // 根据文件扩展名选择解压方法
        string fileExtension = Path.GetExtension(compressedFilePath).ToLowerInvariant();

        try
        {
            switch (fileExtension)
            {
                case ".zip":
                    ExtractZip(compressedFilePath, destinationDirectory);
                    break;
                case ".gz":
                case ".tgz":
                    ExtractTgz(compressedFilePath, destinationDirectory);
                    break;
                case ".tar":
                    ExtractTar(compressedFilePath, destinationDirectory);
                    break;
                case ".rar":
                    ExtractRar(compressedFilePath, destinationDirectory);
                    break;
                default:
                    throw new NotSupportedException($"不支持的压缩格式: {fileExtension}");
            }
        }
        catch (Exception ex)
        {
            LoggerHelper.Warning($"首次解压失败，尝试备选方案: {ex.Message}");
            return TryExtractWithReaderFactory(compressedFilePath, destinationDirectory);
        }
        return true;
    }

    // 解压ZIP文件
    private static void ExtractZip(string zipFilePath, string destinationDirectory)
    {
        using (var archive = ArchiveFactory.OpenArchive(zipFilePath))
        {
            foreach (var entry in archive.Entries)
            {
                if (!entry.IsDirectory)
                {
                    GetExtractionPath(destinationDirectory, entry);
                    entry.WriteToDirectory(destinationDirectory, new ExtractionOptions
                    {
                        ExtractFullPath = true,
                        Overwrite = true
                    });
                }
            }
        }
    }

    // 解压TGZ文件
    private static void ExtractTgz(string tgzFilePath, string destinationDirectory)
    {
        using Stream stream = File.OpenRead(tgzFilePath);
        using var reader = ReaderFactory.OpenReader(stream);

        while (reader.MoveToNextEntry())
        {
            if (!reader.Entry.IsDirectory)
            {
                GetExtractionPath(destinationDirectory, reader.Entry);
                reader.WriteEntryToDirectory(destinationDirectory, new ExtractionOptions
                {
                    ExtractFullPath = true,
                    Overwrite = true
                });
            }
        }
    }

    // 解压TAR文件
    private static void ExtractTar(string tarFilePath, string destinationDirectory)
    {
        using (var archive = TarArchive.OpenArchive(tarFilePath))
        {
            foreach (var entry in archive.Entries)
            {
                if (!entry.IsDirectory)
                {
                    GetExtractionPath(destinationDirectory, entry);
                    entry.WriteToDirectory(destinationDirectory, new ExtractionOptions
                    {
                        ExtractFullPath = true,
                        Overwrite = true
                    });
                }
            }
        }
    }

    // 解压RAR文件
    private static void ExtractRar(string rarFilePath, string destinationDirectory)
    {
        using (var archive = ArchiveFactory.OpenArchive(rarFilePath))
        {
            foreach (var entry in archive.Entries)
            {
                if (!entry.IsDirectory)
                {
                    GetExtractionPath(destinationDirectory, entry);
                    entry.WriteToDirectory(destinationDirectory, new ExtractionOptions
                    {
                        ExtractFullPath = true,
                        Overwrite = true
                    });
                }
            }
        }
    }


    public async static Task<bool> ExtractAsync(string compressedFilePath, string destinationDirectory, ProgressBar? progressBar = null)
    {
        try
        {
            // 创建目标目录
            Directory.CreateDirectory(destinationDirectory);

            var fileExtension = Path.GetExtension(compressedFilePath).ToLowerInvariant();
            switch (fileExtension)
            {
                case ".zip":
                    await ExtractZipAsync(compressedFilePath, destinationDirectory, progressBar);
                    break;
                case ".gz":
                case ".tgz":
                    await ExtractTgzAsync(compressedFilePath, destinationDirectory, progressBar);
                    break;
                case ".tar":
                    await ExtractTarAsync(compressedFilePath, destinationDirectory, progressBar);
                    break;
                case ".rar":
                    await ExtractRarAsync(compressedFilePath, destinationDirectory, progressBar);
                    break;
                default:
                    throw new NotSupportedException($"不支持的压缩格式: {fileExtension}");
            }
            return true;
        }
        catch (Exception ex)
        {
            LoggerHelper.Warning($"首次解压失败，尝试备选方案: {ex.Message}");
            return await TryExtractWithReaderFactoryAsync(compressedFilePath, destinationDirectory, progressBar);

        }
    }

    // 异步解压ZIP文件（带进度）
    async private static Task ExtractZipAsync(string zipFilePath, string destinationDirectory, ProgressBar? progressBar = null)
    {
        using (var archive = ArchiveFactory.OpenArchive(zipFilePath))
        {
            var totalEntries = archive.Entries.Count();
            int processedEntries = 0;

            foreach (var entry in archive.Entries)
            {
                if (!entry.IsDirectory)
                {
                    GetExtractionPath(destinationDirectory, entry);
                    entry.WriteToDirectory(destinationDirectory, new ExtractionOptions
                    {
                        ExtractFullPath = true,
                        Overwrite = true
                    });
                }

                processedEntries++;
                double progress = (double)processedEntries / totalEntries * 100;
                SetProgress(progressBar, progress);

                // 允许UI线程更新
                await Task.Yield();
            }
        }
    }

    // 异步解压TarGz文件（带进度）
    async private static Task ExtractTgzAsync(string tgzFilePath, string destinationDirectory, ProgressBar? progressBar = null)
    {
        using (var archive = TarArchive.OpenArchive(tgzFilePath))
        {
            var totalEntries = archive.Entries.Count();
            int processedEntries = 0;

            foreach (var entry in archive.Entries)
            {
                if (!entry.IsDirectory)
                {
                    GetExtractionPath(destinationDirectory, entry);
                    entry.WriteToDirectory(destinationDirectory, new ExtractionOptions
                    {
                        ExtractFullPath = true,
                        Overwrite = true
                    });
                }

                processedEntries++;
                double progress = (double)processedEntries / totalEntries * 100;
                SetProgress(progressBar, progress);

                // 允许UI线程更新
                await Task.Yield();
            }
        }
    }


    // 异步解压Tar文件（带进度）
    private static async Task ExtractTarAsync(string tarFilePath, string destinationDirectory, ProgressBar? progressBar = null)
    {
        using (var archive = TarArchive.OpenArchive(tarFilePath))
        {
            var totalEntries = archive.Entries.Count();
            int processedEntries = 0;

            foreach (var entry in archive.Entries)
            {
                if (!entry.IsDirectory)
                {
                    GetExtractionPath(destinationDirectory, entry);
                    entry.WriteToDirectory(destinationDirectory, new ExtractionOptions
                    {
                        ExtractFullPath = true,
                        Overwrite = true
                    });
                }

                processedEntries++;
                double progress = (double)processedEntries / totalEntries * 100;
                SetProgress(progressBar, progress);

                // 允许UI线程更新
                await Task.Yield();
            }
        }
    }

    // 异步解压RAR文件（带进度）
    private static async Task ExtractRarAsync(string rarFilePath, string destinationDirectory, ProgressBar? progressBar = null)
    {
        using (var archive = ArchiveFactory.OpenArchive(rarFilePath))
        {
            var totalEntries = archive.Entries.Count();
            int processedEntries = 0;

            foreach (var entry in archive.Entries)
            {
                if (!entry.IsDirectory)
                {
                    GetExtractionPath(destinationDirectory, entry);
                    entry.WriteToDirectory(destinationDirectory, new ExtractionOptions
                    {
                        ExtractFullPath = true,
                        Overwrite = true
                    });
                }

                processedEntries++;
                double progress = (double)processedEntries / totalEntries * 100;
                SetProgress(progressBar, progress);

                // 允许UI线程更新
                await Task.Yield();
            }
        }
    }

    /// <summary>
    /// 同步通用解压（基于ReaderFactory，失败时备选）
    /// </summary>
    private static bool TryExtractWithReaderFactory(
        string compressedFilePath,
        string destinationDirectory,
        ProgressBar? progressBar = null)
    {
        try
        {
            // 第一次遍历：获取总条目数（用于进度计算）
            int totalEntries = 0;
            using (Stream stream = File.OpenRead(compressedFilePath))
            using (var reader = ReaderFactory.OpenReader(stream))
            {
                while (reader.MoveToNextEntry())
                {
                    totalEntries++;
                }
            }

            // 第二次遍历：实际解压并更新进度
            using (Stream stream = File.OpenRead(compressedFilePath))
            using (var reader = ReaderFactory.OpenReader(stream))
            {
                int processedEntries = 0;
                while (reader.MoveToNextEntry())
                {
                    if (!reader.Entry.IsDirectory)
                    {
                        // 构建目标文件路径
                        string outputPath = GetExtractionPath(destinationDirectory, reader.Entry);
                        Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);

                        // 复制条目流到目标文件
                        using (var entryStream = reader.OpenEntryStream())
                        using (var outputStream = File.Create(outputPath))
                        {
                            entryStream.CopyTo(outputStream);
                        }
                    }

                    // 更新进度
                    processedEntries++;
                    double progress = (double)processedEntries / totalEntries * 100;
                    SetProgress(progressBar, progress);
                }
            }
            return true;
        }
        catch (Exception ex)
        {
            LoggerHelper.Error($"ReaderFactory 同步解压失败: {ex.Message}");
            return false;
        }
    }

    /// <summary>
    /// 异步通用解压（基于ReaderFactory，失败时备选）
    /// </summary>
    private static async Task<bool> TryExtractWithReaderFactoryAsync(
        string compressedFilePath,
        string destinationDirectory,
        ProgressBar? progressBar = null)
    {
        try
        {
            // 第一次遍历：获取总条目数（用于进度计算）
            int totalEntries = 0;
            using (Stream stream = File.OpenRead(compressedFilePath))
            using (var reader = ReaderFactory.OpenReader(stream))
            {
                while (reader.MoveToNextEntry())
                {
                    totalEntries++;
                }
            }

            // 第二次遍历：实际解压并更新进度
            using (Stream stream = File.OpenRead(compressedFilePath))
            using (var reader = ReaderFactory.OpenReader(stream))
            {
                int processedEntries = 0;
                while (reader.MoveToNextEntry())
                {
                    if (!reader.Entry.IsDirectory)
                    {
                        // 构建目标文件路径
                        string outputPath = GetExtractionPath(destinationDirectory, reader.Entry);
                        Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);

                        // 异步复制条目流到目标文件
                        using (var entryStream = reader.OpenEntryStream())
                        using (var outputStream = File.Create(outputPath))
                        {
                            await entryStream.CopyToAsync(outputStream).ConfigureAwait(false);
                        }
                    }

                    // 更新进度（确保UI线程执行）
                    processedEntries++;
                    double progress = (double)processedEntries / totalEntries * 100;
                    SetProgress(progressBar, progress);

                    // 让出线程，避免阻塞UI
                    await Task.Yield();
                }
            }
            return true;
        }
        catch (Exception ex)
        {
            LoggerHelper.Error($"ReaderFactory 异步解压失败: {ex.Message}");
            return false;
        }
    }

    // 设置进度条（与你现有代码保持一致）
    private static void SetProgress(ProgressBar? progressBar, double value)
    {
        if (progressBar != null)
        {
            DispatcherHelper.RunOnMainThread(() =>
            {
                progressBar.Value = value;
            });
        }
    }
}
