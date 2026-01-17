# Test Case Attachments

This document describes the attachment functionality implemented for the NUnit test framework integration.

## Overview

The system supports uploading, storing, and downloading attachments for individual test cases. Each test case now has an internal storage identifier (similar to a run ID) that maps the original test case name to filesystem paths. Attachments live inside the case folder (`cases/{storage_id}/attachments/`) within a run directory and remain accessible through the web UI and HTTP API using the full test case name.

## Features

### 1. NUnit Plugin Support
- **Wrapper-based attachments**: Add attachments using `TestContextWrapper.AddTestAttachment()`. The wrapper forwards the call to `TestContext.AddTestAttachment()` so NUnit still tracks the attachment.
- **Upload integration**: Attachments added through the wrapper are uploaded to the server while the test run is active.
- **Error handling**: Failed uploads are logged but don't affect test execution.

### 2. Server API Endpoints

#### Upload Attachments
```
POST /api/attachments/{run_id}/{test_case_id}/upload
Content-Type: multipart/form-data

Form data:
- attachment: file (required)
- description: string (optional)
```

#### List Attachments
```
GET /api/attachments/{run_id}/{test_case_id}/list
Response: {"attachments": [{"filename": "...", "size": 123, "modified_time": "..."}]}
```

#### Download Attachments
```
GET /api/attachments/{run_id}/{test_case_id}/download/{filename}
Response: file content with appropriate headers
```

### 3. UI Integration

#### Test Case Log Page
- **Attachments section**: The test case log page shows an “Attachments” section under **Test Details**.
- **Attachment list**: Each attachment is shown with a file icon, filename, and formatted size. Clicking an attachment downloads the file.
- **Offline view**: When you export a run as a ZIP archive, the attachments are included and listed in the offline log pages.

#### Visual Features
- File type icons (📄 for PDFs, 🖼️ for images, etc.)
- File size formatting (Bytes, KB, MB, GB)
- Hover effects and responsive design
- Dark theme support

### 4. ZIP Export Integration
- Attachments are automatically included in ZIP file downloads
- Stored under `attachments/{sanitized_test_case_name}/` in the ZIP structure for readability
- Maintains the same directory structure as the server

## Usage Examples

### In NUnit Tests

```csharp
using TestRift.NUnit;

[Test]
public void TestWithAttachments()
{
    // Create a test file
    var tempFile = Path.GetTempFileName();
    File.WriteAllText(tempFile, "Test data");
    
    try
    {
        // Add as attachment using our wrapper
        TestContextWrapper.AddTestAttachment(tempFile, "Test data file");
        
        // Your test logic here
        Assert.Pass("Test completed with attachment");
    }
    finally
    {
        // Clean up
        File.Delete(tempFile);
    }
}
```

**Note**: Use `TestContextWrapper.AddTestAttachment()` instead of `TestContext.AddTestAttachment()` to ensure attachments are properly tracked and uploaded to the server.

### API Usage

```python
import requests

# Upload an attachment
with open('test_file.txt', 'rb') as f:
    files = {'attachment': ('test_file.txt', f, 'text/plain')}
    response = requests.post(
        'http://localhost:8080/api/attachments/run123/TestWithAttachments/upload',
        files=files
    )

# List attachments
response = requests.get(
    'http://localhost:8080/api/attachments/run123/TestWithAttachments/list'
)
attachments = response.json()['attachments']

# Download an attachment
response = requests.get(
    'http://localhost:8080/api/attachments/run123/TestWithAttachments/download/test_file.txt'
)
```

## File Storage

### Directory Structure
```
data/
├── {run_id}/
│   ├── meta.json
│   └── cases/
│       └── {storage_id}/
│           ├── log.jsonl
│           ├── stack.jsonl
│           └── attachments/
│               ├── attachment1.txt
│               ├── screenshot.png
│               └── log_file.log
```

### File Naming
- Original filenames are preserved.
- Invalid characters are sanitized for filesystem compatibility.
- Duplicate names for the same test case overwrite the previous file after sanitization.
- On disk, attachment folders are keyed by the generated storage ID; when exporting a ZIP we continue to group attachments under `attachments/{sanitized_test_case_name}/` for readability.

## Security Considerations

- **Access control**: Attachments are only accessible for valid test runs.
- **File validation**: The server validates file existence and permissions before serving downloads.
- **Path sanitization**: Filenames are sanitized to prevent directory traversal.
- **Size limits**: Uploads are limited by the `attachments.max_size` setting in `testrift_server.yaml` (default `10MB`). Files larger than this limit are rejected with HTTP `413` and an explanatory message.
- **Feature toggle**: When `attachments.enabled` is set to `false` in `testrift_server.yaml`, upload requests are rejected with HTTP `403`.

## Error Handling

### Client Side (NUnit Plugin)
- File not found errors are logged
- Network errors are logged but don't fail tests
- Upload failures are logged with details

### Server Side
- Invalid run/test case IDs or filenames return `400` (bad request) or `404` (not found), depending on the condition.
- Uploads that exceed the configured size limit return `413` (payload too large).
- Uploads are rejected with `403` when attachment upload is disabled in configuration.
- Other upload errors return `500` with error details, and are logged on the server.
- All errors are logged for debugging

## Testing

You can test the attachment functionality by running the NUnit tests with the attachment example:

```bash
cd nunit/Example
dotnet test
```

This will run the `TestWithAttachments` test which:
1. Creates a temporary test file
2. Adds it as an attachment using `TestContextWrapper.AddTestAttachment()`
3. The attachment is automatically uploaded to the server
4. You can view it in the web UI at the test case log page
5. Download the attachment by clicking on the filename

## Dependencies

### Server
- `aiofiles>=23.0.0` - For async file operations
- `aiohttp>=3.8.0` - For HTTP handling

### Client (NUnit Plugin)
- `System.Net.Http` - For HTTP client operations
- `NUnit.Framework` - For test context and attachments

## Configuration

Attachment upload behavior is controlled by the `attachments` section in `testrift_server.yaml`
(see [server_config.md](server_config.md) for the full list of options and defaults). The NUnit 
client simply honors those limits and settings when uploading files.
