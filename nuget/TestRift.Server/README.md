# TestRift.Server NuGet Package

Python server for TestRift real-time test runs: live log streaming, result storage, and a web UI for browsing and analysis.

**Designed for use with [TestRift.NUnit](https://www.nuget.org/packages/TestRift.NUnit)** - provides automatic server startup and configuration for NUnit test projects.

![Tests](https://github.com/testrift/testrift-server/actions/workflows/tests.yml/badge.svg)

## Experimental

TestRift is currently in an **experimental** phase. APIs, configuration, and data formats may change at any time **without notice**.

## What's Included

This NuGet package contains:
- TestRift Server Python source code
- Platform-specific launcher scripts (`testrift-server.bat`, `testrift-server.sh`)
- Automatic Python virtual environment management
- All required Python dependencies (aiohttp, aiofiles, PyYAML, jinja2, aiosqlite)

## Installation

Add the package reference to your test project:

```xml
<PackageReference Include="TestRift.Server" Version="*" />
```

The package will be automatically extracted to your NuGet cache during restore.

**Note:** This package is typically installed alongside TestRift.NUnit, which provides the integration for NUnit test projects.

## Usage

### Automatic Server Start with TestRift.NUnit (Recommended)

When using TestRift.NUnit, the server starts automatically when tests run with `autoStartServer` configured.

#### 1. Configure TestRift.NUnit to auto-start the server

**TestRiftNUnit.yaml:**
```yaml
serverUrl: http://localhost:8080
autoStartServer:
  enabled: true
  serverYaml: TestRiftServer.yaml
  restartOnConfigChange: true
```

#### 2. Create server configuration file

**TestRiftServer.yaml:**
```yaml
server:
  port: 8080
  localhost_only: true
```

The TestRift.NUnit plugin will:
1. Locate the TestRift.Server package in your NuGet cache
2. Run the launcher script with the configuration from `serverYaml`
3. Wait for the server to become healthy
4. Run your tests
5. Keep the server running until tests complete

This is the **recommended approach** - server configuration is separated into its own file referenced by the `serverYaml` property.

### Manual Server Start

You can also run the server manually from the NuGet package location:

**Windows:**
```cmd
%USERPROFILE%\.nuget\packages\testrift.server\<version>\tools\testrift-server.bat
```

**Linux/macOS:**
```bash
~/.nuget/packages/testrift.server/<version>/tools/testrift-server.sh
```

## Virtual Environment Management

The launcher scripts automatically manage a Python virtual environment:

1. **First run:** Creates `.venv` in the package tools directory
2. **Dependency installation:** Installs required packages from `requirements.txt`
3. **Smart updates:** Only reinstalls dependencies when `requirements.txt` changes
4. **Isolated environment:** Each package version has its own venv

No manual pip installation or Python environment setup required!

## Configuration

### Recommended: Using TestRift.NUnit's serverYaml

The **recommended approach** is to configure the server using a separate YAML file referenced in your TestRift.NUnit configuration:

**TestRiftNUnit.yaml:**
```yaml
serverUrl: http://localhost:8080
autoStartServer:
  enabled: true
  serverYaml: TestRiftServer.yaml  # Server config file
```

**TestRiftServer.yaml:**
```yaml
server:
  port: 8080
  localhost_only: true

logging:
  level: INFO
```

This keeps server configuration separate from test configuration and allows the TestRift.NUnit plugin to manage the server's lifecycle automatically.

### Alternative: Environment Variable

When running the server manually, you can specify configuration via environment variable:
- Set `TESTRIFT_SERVER_YAML` to an absolute path to a YAML config file
- The server will fail to start if the file doesn't exist

### Configuration File Search

If no configuration is specified, the server looks for `testrift_server.yaml` in the current working directory.

For the full configuration reference, see the [server_config.md](https://github.com/testrift/testrift-server/blob/main/docs/server_config.md) documentation.

## Requirements

- **Python 3.8+** must be available on PATH
  - Windows: `python` or `python3` command
  - Unix: `python3` command
- **No pip installation required** - dependencies are managed automatically

## Package Structure

```
~/.nuget/packages/testrift.server/<version>/
├── tools/
│   ├── testrift-server.bat      # Windows launcher
│   ├── testrift-server.sh       # Unix launcher
│   ├── server/
│   │   ├── requirements.txt     # Python dependencies
│   │   └── testrift_server/     # Server source code
│   └── .venv/                   # Auto-created virtual environment
│       ├── Scripts/             # (Windows) Python executables
│       ├── bin/                 # (Unix) Python executables
│       └── Lib/                 # Installed Python packages
└── LICENSE
```

## Troubleshooting

### Server won't start

**Check Python is available:**
```bash
python --version  # or python3 --version
```

**Check logs:** When auto-started, error messages appear in test output diagnostics.

### Dependencies not installing

The launcher scripts automatically run `pip install -r requirements.txt` on first use. If this fails:
- Ensure you have internet connectivity
- Check Python has pip installed: `python -m pip --version`
- Manually delete `.venv` folder to force recreation

### Port conflicts

If port 8080 is in use, configure a different port:

```yaml
# TestRiftServer.yaml
server:
  port: 19999
```

Update your test configuration to match:

```yaml
# TestRiftNUnit.yaml
server:
  url: http://localhost:19999
```

## Links

- [TestRift.Server GitHub](https://github.com/testrift/testrift-server)
- [TestRift.NUnit GitHub](https://github.com/testrift/testrift-nunit)
- [Server Configuration Reference](https://github.com/testrift/testrift-server/blob/main/docs/server_config.md)

## License

Apache-2.0
