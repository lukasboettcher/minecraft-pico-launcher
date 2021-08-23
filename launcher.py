from typing import cast, Generator, Callable, Optional, Tuple, Dict, Type, List
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse
from urllib import parse as url_parse, request as url_request
from urllib.request import Request as UrlRequest
from urllib.error import HTTPError
from json import JSONDecodeError
from zipfile import ZipFile
from uuid import uuid4
from os import path
import platform
import hashlib
import shutil
# import base64
import json
import sys
import os
import re
from typing import cast, Union, Any, List, Dict, Optional, Type, Tuple
from argparse import ArgumentParser, Namespace
from urllib import parse as url_parse
from json import JSONDecodeError
from datetime import datetime
from types import ModuleType
from os import path
import platform
import shutil
import json
import sys
import os


LAUNCHER_NAME = "portablemc"
LAUNCHER_VERSION = "1.0.0"

class Context:

    def __init__(self, main_dir: Optional[str] = None, work_dir: Optional[str] = None):
        main_dir = get_minecraft_dir() if main_dir is None else path.realpath(main_dir)
        self.work_dir = main_dir if work_dir is None else path.realpath(work_dir)
        self.versions_dir = path.join(main_dir, "versions")
        self.assets_dir = path.join(main_dir, "assets")
        self.libraries_dir = path.join(main_dir, "libraries")
        self.jvm_dir = path.join(main_dir, "jvm")
        self.bin_dir = path.join(self.work_dir, "bin")

    def has_version_metadata(self, version: str) -> bool:
        """ Return True if the given version has a metadata file. """
        return path.isfile(path.join(self.versions_dir, version, f"{version}.json"))

    def get_version_dir(self, version_id: str) -> str:
        return path.join(self.versions_dir, version_id)

    def list_versions(self) -> Generator[Tuple[str, int], None, None]:
        """ A generator method that yields all versions (version, mtime) that have a version metadata file. """
        if path.isdir(self.versions_dir):
            for version in os.listdir(self.versions_dir):
                try:
                    yield version, path.getmtime(path.join(self.versions_dir, version, f"{version}.json"))
                except OSError:
                    pass


class Version:

    def __init__(self, context: Context, version_id: str):

        """ Construct a new version, using a specific context and the exact version ID you want to start. """

        self.context = context
        self.id = version_id

        self.manifest: Optional[VersionManifest] = None
        self.dl = DownloadList()

        self.version_meta: Optional[dict] = None
        self.version_dir: Optional[str] = None
        self.version_jar_file: Optional[str] = None

        self.assets_index_version: Optional[int] = None
        self.assets_virtual_dir: Optional[str] = None
        self.assets_count: Optional[int] = None

        self.logging_file: Optional[str] = None
        self.logging_argument: Optional[str] = None

        self.classpath_libs: List[str] = []
        self.native_libs: List[str] = []

        self.jvm_version: Optional[str] = None
        self.jvm_exec: Optional[str] = None

    def prepare_meta(self, *, recursion_limit: int = 50):

        version_meta, version_dir = self._prepare_meta_internal(self.id)
        while "inheritsFrom" in version_meta:
            if recursion_limit <= 0:
                raise VersionError(VersionError.TO_MUCH_PARENTS, self.id)
            recursion_limit -= 1
            parent_meta, _ = self._prepare_meta_internal(version_meta["inheritsFrom"])
            del version_meta["inheritsFrom"]
            merge_dict(version_meta, parent_meta)

        self.version_meta, self.version_dir = version_meta, version_dir

    def _prepare_meta_internal(self, version_id: str) -> Tuple[dict, str]:

        version_dir = self.context.get_version_dir(version_id)
        version_meta_file = path.join(version_dir, f"{version_id}.json")

        try:
            with open(version_meta_file, "rt") as version_meta_fp:
                return json.load(version_meta_fp), version_dir
        except (OSError, JSONDecodeError):
            version_super_meta = self._ensure_version_manifest().get_version(version_id)
            if version_super_meta is not None:
                content = json_simple_request(version_super_meta["url"])
                os.makedirs(version_dir, exist_ok=True)
                with open(version_meta_file, "wt") as version_meta_fp:
                    json.dump(content, version_meta_fp, indent=2)
                return content, version_dir
            else:
                for version_data in self.manifest.all_versions():
                    version_id = version_data["id"]
                    date = datetime.fromisoformat(version_data['releaseTime']).strftime('%Y-%m-%d')
                    if version_data['type'] == 'release':
                        print ("%20s %20s %20s" % (version_id, version_data['type'], date))
                    
                print('These are the supported releases')
                sys.exit(1)
                raise VersionError(VersionError.NOT_FOUND, version_id)

    def _ensure_version_manifest(self) -> 'VersionManifest':
        if self.manifest is None:
            self.manifest = VersionManifest.load_from_url()
        return self.manifest

    def _check_version_meta(self):
        if self.version_meta is None:
            raise ValueError("You should install metadata first.")

    def prepare_jar(self):

        self._check_version_meta()
        self.version_jar_file = path.join(self.version_dir, f"{self.id}.jar")
        client_download = self.version_meta.get("downloads", {}).get("client")
        if client_download is not None:
            entry = DownloadEntry.from_meta(client_download, self.version_jar_file, name=f"{self.id}.jar")
            if not path.isfile(entry.dst) or path.getsize(entry.dst) != entry.size:
                self.dl.append(entry)
        elif not path.isfile(self.version_jar_file):
            raise VersionError(VersionError.JAR_NOT_FOUND, self.id)

    def prepare_assets(self):

        self._check_version_meta()

        assets_indexes_dir = path.join(self.context.assets_dir, "indexes")
        asset_index_info = self.version_meta.get("assetIndex")
        if asset_index_info is None:
            return

        assets_index_version = self.version_meta.get("assets", asset_index_info.get("id", None))
        if assets_index_version is None:
            return

        assets_index_file = path.join(assets_indexes_dir, f"{assets_index_version}.json")

        try:
            with open(assets_index_file, "rb") as assets_index_fp:
                assets_index = json.load(assets_index_fp)
        except (OSError, JSONDecodeError):
            asset_index_url = asset_index_info["url"]
            assets_index = json_simple_request(asset_index_url)
            os.makedirs(assets_indexes_dir, exist_ok=True)
            with open(assets_index_file, "wt") as assets_index_fp:
                json.dump(assets_index, assets_index_fp)

        assets_objects_dir = path.join(self.context.assets_dir, "objects")
        assets_virtual_dir = path.join(self.context.assets_dir, "virtual", assets_index_version)
        assets_mapped_to_resources = assets_index.get("map_to_resources", False)  # For version <= 13w23b
        assets_virtual = assets_index.get("virtual", False)  # For 13w23b < version <= 13w48b (1.7.2)

        for asset_id, asset_obj in assets_index["objects"].items():
            asset_hash = asset_obj["hash"]
            asset_hash_prefix = asset_hash[:2]
            asset_size = asset_obj["size"]
            asset_file = path.join(assets_objects_dir, asset_hash_prefix, asset_hash)
            if not path.isfile(asset_file) or path.getsize(asset_file) != asset_size:
                asset_url = f"https://resources.download.minecraft.net/{asset_hash_prefix}/{asset_hash}"
                self.dl.append(DownloadEntry(asset_url, asset_file, size=asset_size, sha1=asset_hash, name=asset_id))

        def finalize():
            if assets_mapped_to_resources or assets_virtual:
                for asset_id_to_cpy in assets_index["objects"].keys():
                    if assets_mapped_to_resources:
                        resources_asset_file = path.join(self.context.work_dir, "resources", asset_id_to_cpy)
                        if not path.isfile(resources_asset_file):
                            os.makedirs(path.dirname(resources_asset_file), exist_ok=True)
                            shutil.copyfile(asset_file, resources_asset_file)
                    if assets_virtual:
                        virtual_asset_file = path.join(assets_virtual_dir, asset_id_to_cpy)
                        if not path.isfile(virtual_asset_file):
                            os.makedirs(path.dirname(virtual_asset_file), exist_ok=True)
                            shutil.copyfile(asset_file, virtual_asset_file)

        self.dl.add_callback(finalize)
        self.assets_index_version = assets_index_version
        self.assets_virtual_dir = assets_virtual_dir
        self.assets_count = len(assets_index["objects"])

    def prepare_logger(self):

        self._check_version_meta()
        client_logging = self.version_meta.get("logging", {}).get("client")
        if client_logging is not None:
            logging_file_info = client_logging["file"]
            logging_file = path.join(self.context.assets_dir, "log_configs", logging_file_info["id"])
            download_entry = DownloadEntry.from_meta(logging_file_info, logging_file, name=logging_file_info["id"])
            if not path.isfile(logging_file) or path.getsize(logging_file) != download_entry.size:
                self.dl.append(download_entry)
            self.logging_file = logging_file
            self.logging_argument = client_logging["argument"]

    def prepare_libraries(self):

        self._check_version_meta()

        if self.version_jar_file is None:
            raise ValueError("The version JAR file is not ")

        self.classpath_libs.clear()
        self.native_libs.clear()

        for lib_obj in self.version_meta["libraries"]:

            if "rules" in lib_obj:
                if not interpret_rule(lib_obj["rules"]):
                    continue

            lib_name: str = lib_obj["name"]
            lib_dl_name = lib_name
            lib_natives: Optional[dict] = lib_obj.get("natives")

            if lib_natives is not None:
                lib_classifier = lib_natives.get(get_minecraft_os())
                if lib_classifier is None:
                    continue  # If natives are defined, but the OS is not supported, skip.
                lib_dl_name += f":{lib_classifier}"
                archbits = get_minecraft_archbits()
                if len(archbits):
                    lib_classifier = lib_classifier.replace("${arch}", archbits)
                lib_libs = self.native_libs
            else:
                lib_classifier = None
                lib_libs = self.classpath_libs

            lib_path: Optional[str] = None
            lib_dl_entry: Optional[DownloadEntry] = None
            lib_dl: Optional[dict] = lib_obj.get("downloads")

            if lib_dl is not None:

                if lib_classifier is not None:
                    lib_dl_classifiers = lib_dl.get("classifiers")
                    lib_dl_meta = None if lib_dl_classifiers is None else lib_dl_classifiers.get(lib_classifier)
                else:
                    lib_dl_meta = lib_dl.get("artifact")

                if lib_dl_meta is not None:
                    lib_path = path.join(self.context.libraries_dir, lib_dl_meta["path"])
                    lib_dl_entry = DownloadEntry.from_meta(lib_dl_meta, lib_path, name=lib_dl_name)

            if lib_dl_entry is None:

                lib_name_parts = lib_name.split(":")
                if len(lib_name_parts) != 3:
                    continue  # If the library name is not maven-formatted, skip.

                vendor, package, version = lib_name_parts
                jar_file = f"{package}-{version}.jar" if lib_classifier is None else f"{package}-{version}-{lib_classifier}.jar"
                lib_path_raw = "/".join((*vendor.split("."), package, version, jar_file))
                lib_path = path.join(self.context.libraries_dir, lib_path_raw)

                if not path.isfile(lib_path):
                    lib_repo_url: Optional[str] = lib_obj.get("url")
                    if lib_repo_url is None:
                        continue  # If the file doesn't exists, and no server url is provided, skip.
                    lib_dl_entry = DownloadEntry(f"{lib_repo_url}{lib_path_raw}", lib_path, name=lib_dl_name)

            lib_libs.append(lib_path)
            if lib_dl_entry is not None and (not path.isfile(lib_path) or path.getsize(lib_path) != lib_dl_entry.size):
                self.dl.append(lib_dl_entry)

        self.classpath_libs.append(self.version_jar_file)

    def prepare_jvm(self):

        self._check_version_meta()
        jvm_version_type = self.version_meta.get("javaVersion", {}).get("component", "jre-legacy")

        all_jvm_meta = json_simple_request("https://launchermeta.mojang.com/v1/products/java-runtime/2ec0cc96c44e5a76b9c8b7c39df7210883d12871/all.json")
        jvm_arch_meta = all_jvm_meta.get(get_minecraft_jvm_os())
        if jvm_arch_meta is None:
            raise JvmLoadingError(JvmLoadingError.UNSUPPORTED_ARCH)

        jvm_meta = jvm_arch_meta.get(jvm_version_type)
        if jvm_meta is None:
            raise JvmLoadingError(JvmLoadingError.UNSUPPORTED_VERSION)

        jvm_dir = path.join(self.context.jvm_dir, jvm_version_type)
        jvm_manifest = json_simple_request(jvm_meta[0]["manifest"]["url"])["files"]
        self.jvm_version = jvm_meta[0]["version"]["name"]
        self.jvm_exec = path.join(jvm_dir, "bin", "javaw.exe" if sys.platform == "win32" else "java")

        if not path.isfile(self.jvm_exec):

            jvm_exec_files = []
            os.makedirs(jvm_dir, exist_ok=True)
            for jvm_file_path_suffix, jvm_file in jvm_manifest.items():
                if jvm_file["type"] == "file":
                    jvm_file_path = path.join(jvm_dir, jvm_file_path_suffix)
                    jvm_download_info = jvm_file["downloads"]["raw"]
                    self.dl.append(DownloadEntry.from_meta(jvm_download_info, jvm_file_path, name=jvm_file_path_suffix))
                    if jvm_file.get("executable", False):
                        jvm_exec_files.append(jvm_file_path)

            def finalize():
                for exec_file in jvm_exec_files:
                    os.chmod(exec_file, 0o777)

            self.dl.add_callback(finalize)

    def download(self):
        """ Download all missing files computed in `prepare_` methods. """
        self.dl.download_files()
        self.dl.reset()

    def install(self, *, jvm: bool = False):
        """ Prepare (meta, jar, assets, logger, libs, jvm) and download the version with optional JVM installation. """
        self.prepare_meta()
        self.prepare_jar()
        self.prepare_assets()
        self.prepare_logger()
        self.prepare_libraries()
        if jvm:
            self.prepare_jvm()
        self.download()

    def start(self, opts: 'Optional[StartOptions]' = None):
        """ Faster method to start the version. This actually use `Start` class, however, you can use it directly. """
        start = Start(self)
        start.prepare(opts or StartOptions())
        start.start()


class StartOptions:

    def __init__(self):
        # self.auth_session: Optional[AuthSession] = None
        self.uuid: Optional[str] = None
        self.username: Optional[str] = None
        self.demo: bool = False
        self.resolution: Optional[Tuple[int, int]] = None
        self.disable_multiplayer: bool = False
        self.disable_chat: bool = False
        self.server_address: Optional[str] = None
        self.server_port: Optional[int] = None
        self.jvm_exec: Optional[str] = None
        self.features: Dict[str, bool] = {}  # Additional features

class Start:

    def __init__(self, version: Version):

        self.version = version

        self.args_replacements: Dict[str, str] = {}
        self.main_class: Optional[str] = None
        self.jvm_args: List[str] = []
        self.game_args: List[str] = []

        self.bin_dir_factory: Callable[[str], str] = self.default_bin_dir_factory
        self.runner: Callable[[List[str], str], None] = self.default_runner

    def _check_version(self):
        if self.version.version_meta is None:
            raise ValueError("You should install the version metadata first.")

    def get_username(self) -> str:
        return self.args_replacements.get("auth_player_name", "n/a")

    def get_uuid(self) -> str:
        return self.args_replacements.get("auth_uuid", "n/a")

    def prepare(self, opts: StartOptions):

        self._check_version()

        # Main class
        self.main_class = self.version.version_meta.get("mainClass")
        if self.main_class is None:
            raise ValueError("The version metadata has no main class to start.")

        # Prepare JVM exec
        jvm_exec = opts.jvm_exec
        if jvm_exec is None:
            jvm_exec = self.version.jvm_exec
            if jvm_exec is None:
                raise ValueError("No JVM executable set in options or downloaded by the version.")

        # Features
        features = {
            "is_demo_user": opts.demo,
            "has_custom_resolution": opts.resolution is not None,
            **opts.features
        }

        username_file_path = path.join(get_minecraft_dir(), 'username.dat')
        last_username = None
        if os.path.exists(username_file_path):
            last_username = open(username_file_path).readline()


        uuid = uuid4().hex if opts.uuid is None else opts.uuid.replace("-", "").lower()
        username = uuid[:8] if opts.username is None else opts.username[:16]  # Max username length is 16

        username = opts.username[:16] if opts.username is not None else uuid[:8] if last_username is None else last_username

        with open(username_file_path, 'w') as username_file:
            username_file.write(username)

        print(f"Starting Minecraft with username: {username}")

        # Arguments replacements
        self.args_replacements = {
            # Game
            "auth_player_name": username,
            "version_name": self.version.id,
            "game_directory": self.version.context.work_dir,
            "assets_root": self.version.context.assets_dir,
            "assets_index_name": self.version.assets_index_version,
            "auth_uuid": uuid,
            "auth_access_token": "" ,
            "user_type": "mojang",
            "version_type": self.version.version_meta.get("type", ""),
            # Game (legacy)
            "auth_session": "" ,
            "game_assets": self.version.assets_virtual_dir,
            "user_properties": "{}",
            # JVM
            "natives_directory": "",
            "launcher_name": LAUNCHER_NAME,
            "launcher_version": LAUNCHER_VERSION,
            "classpath": path.pathsep.join(self.version.classpath_libs)
        }

        if opts.resolution is not None:
            self.args_replacements["resolution_width"] = str(opts.resolution[0])
            self.args_replacements["resolution_height"] = str(opts.resolution[1])

        # Arguments
        modern_args = self.version.version_meta.get("arguments", {})
        modern_jvm_args = modern_args.get("jvm")
        modern_game_args = modern_args.get("game")

        self.jvm_args.clear()
        self.game_args.clear()

        # JVM arguments
        self.jvm_args.append(jvm_exec)
        interpret_args(LEGACY_JVM_ARGUMENTS if modern_jvm_args is None else modern_jvm_args, features, self.jvm_args)

        # JVM argument for logging config
        if self.version.logging_argument is not None and self.version.logging_file is not None:
            self.jvm_args.append(self.version.logging_argument.replace("${path}", self.version.logging_file))

        # JVM argument for launch wrapper JAR path
        if self.main_class == "net.minecraft.launchwrapper.Launch":
            self.jvm_args.append(f"-Dminecraft.client.jar={self.version.version_jar_file}")

        # Game arguments
        if modern_game_args is None:
            self.game_args.extend(self.version.version_meta.get("minecraftArguments", "").split(" "))
        else:
            interpret_args(modern_game_args, features, self.game_args)

        if opts.disable_multiplayer:
            self.game_args.append("--disableMultiplayer")
        if opts.disable_chat:
            self.game_args.append("--disableChat")

        if opts.server_address is not None:
            self.game_args.extend(("--server", opts.server_address))
        if opts.server_port is not None:
            self.game_args.extend(("--port", str(opts.server_port)))

    def start(self):


        bin_dir = self.bin_dir_factory(self.version.context.bin_dir)
        cleaned = False

        def cleanup():
            nonlocal cleaned
            if not cleaned:
                shutil.rmtree(bin_dir, ignore_errors=True)
                cleaned = True

        import atexit
        atexit.register(cleanup)

        for native_lib in self.version.native_libs:
            with ZipFile(native_lib, "r") as native_zip:
                for native_zip_info in native_zip.infolist():
                    if can_extract_native(native_zip_info.filename):
                        native_zip.extract(native_zip_info, bin_dir)

        self.args_replacements["natives_directory"] = bin_dir

        command_for_runner = [
            *replace_list_vars(self.jvm_args, self.args_replacements),
            self.main_class,
            *replace_list_vars(self.game_args, self.args_replacements)
        ]

        self.runner(command_for_runner, self.version.context.work_dir)

        cleanup()

    @staticmethod
    def default_bin_dir_factory(common_bin_dir: str) -> str:
        return path.join(common_bin_dir, str(uuid4()))

    @staticmethod
    def default_runner(args: List[str], cwd: str) -> None:
        import subprocess
        subprocess.run(args, cwd=cwd, stdout=subprocess.DEVNULL)


class VersionManifest:

    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load_from_url(cls):
        """ Load the version manifest from the official URL. Can raise `JsonRequestError` if failed. """
        return cls(json_simple_request("https://launchermeta.mojang.com/mc/game/version_manifest.json"))

    def filter_latest(self, version: str) -> Tuple[str, bool]:
        latest = self.data["latest"].get(version)
        return (version, False) if latest is None else (latest, True)

    def get_version(self, version: str) -> Optional[dict]:
        version, _alias = self.filter_latest(version)
        for version_data in self.data["versions"]:
            if version_data["id"] == version:
                return version_data
        return None

    def all_versions(self) -> list:
        return self.data["versions"]



class DownloadEntry:

    __slots__ = "url", "size", "sha1", "dst", "name"

    def __init__(self, url: str, dst: str, *, size: Optional[int] = None, sha1: Optional[str] = None, name: Optional[str] = None):
        self.url = url
        self.dst = dst
        self.size = size
        self.sha1 = sha1
        self.name = url if name is None else name

    @classmethod
    def from_meta(cls, info: dict, dst: str, *, name: Optional[str] = None) -> 'DownloadEntry':
        return DownloadEntry(info["url"], dst, size=info["size"], sha1=info["sha1"], name=name)


class DownloadList:

    __slots__ = "entries", "callbacks", "count", "size"

    def __init__(self):
        self.entries: Dict[str, List[DownloadEntry]] = {}
        self.callbacks: List[Callable[[], None]] = []
        self.count = 0
        self.size = 0

    def append(self, entry: DownloadEntry):
        url_parsed = url_parse.urlparse(entry.url)
        if url_parsed.scheme not in ("http", "https"):
            raise ValueError("Illegal URL scheme for HTTP connection.")
        host_key = f"{int(url_parsed.scheme == 'https')}{url_parsed.netloc}"
        entries = self.entries.get(host_key)
        if entries is None:
            self.entries[host_key] = entries = []
        entries.append(entry)
        self.count += 1
        if entry.size is not None:
            self.size += entry.size

    def reset(self):
        self.entries.clear()
        self.callbacks.clear()

    def add_callback(self, callback: Callable[[], None]):
        self.callbacks.append(callback)

    def download_files(self):

        """
        Downloads the given list of files. Even if some downloads fails, it continue and raise DownloadError(fails)
        only at the end (but not calling callbacks), where 'fails' is a dict associating the entry URL and its error
        ('not_found', 'invalid_size', 'invalid_sha1').
        """

        if len(self.entries):

            headers = {}
            buffer = bytearray(65536)
            total_size = 0
            fails: Dict[str, str] = {}
            max_try_count = 3

            # if progress_callback is not None:
            #     # progress = DownloadProgress(self.size)
            #     # entry_progress = DownloadEntryProgress()
            #     # progress.entries.append(entry_progress)
            #     pass
            # else:
            #     progress = None
            #     entry_progress = None

            for host, entries in self.entries.items():

                conn_type = HTTPSConnection if (host[0] == "1") else HTTPConnection
                conn = conn_type(host[1:])
                max_entry_idx = len(entries) - 1
                headers["Connection"] = "keep-alive"

                for i, entry in enumerate(entries):

                    last_entry = (i == max_entry_idx)
                    if last_entry:
                        headers["Connection"] = "close"

                    # size_target = 0 if entry.size is None else entry.size
                    error = None

                    for _ in range(max_try_count):

                        try:
                            conn.request("GET", entry.url, None, headers)
                            res = conn.getresponse()
                        except ConnectionError:
                            error = DownloadError.CONN_ERROR
                            continue

                        if res.status != 200:
                            error = DownloadError.NOT_FOUND
                            continue

                        sha1 = None if entry.sha1 is None else hashlib.sha1()
                        size = 0

                        os.makedirs(path.dirname(entry.dst), exist_ok=True)
                        with open(entry.dst, "wb") as dst_fp:
                            while True:
                                read_len = res.readinto(buffer)
                                if not read_len:
                                    break
                                buffer_view = buffer[:read_len]
                                size += read_len
                                total_size += read_len
                                if sha1 is not None:
                                    sha1.update(buffer_view)
                                dst_fp.write(buffer_view)
                                # if progress_callback is not None:
                                #     progress.size = total_size
                                #     entry_progress.name = entry.name
                                #     entry_progress.total = size_target
                                #     entry_progress.size = size
                                #     progress_callback(progress)

                        if entry.size is not None and size != entry.size:
                            error = DownloadError.INVALID_SIZE
                        elif entry.sha1 is not None and sha1.hexdigest() != entry.sha1:
                            error = DownloadError.INVALID_SHA1
                        else:
                            break

                        total_size -= size  # If error happened, subtract the size and restart from latest total_size.

                    else:
                        fails[entry.url] = error  # If the break was not triggered, an error should be set.

                conn.close()

            if len(fails):
                raise DownloadError(fails)

        for callback in self.callbacks:
            callback()


class BaseError(Exception):

    def __init__(self, code: str):
        super().__init__()
        self.code = code


class JsonRequestError(BaseError):

    INVALID_RESPONSE_NOT_JSON = "invalid_response_not_json"

    def __init__(self, code: str, details: str):
        super().__init__(code)
        self.details = details


class AuthError(BaseError):

    YGGDRASIL = "yggdrasil"
    MICROSOFT = "microsoft"
    MICROSOFT_INCONSISTENT_USER_HASH = "microsoft.inconsistent_user_hash"
    MICROSOFT_DOES_NOT_OWN_MINECRAFT = "microsoft.does_not_own_minecraft"
    MICROSOFT_OUTDATED_TOKEN = "microsoft.outdated_token"

    def __init__(self, code: str, details: Optional[str] = None):
        super().__init__(code)
        self.details = details


class VersionError(BaseError):

    NOT_FOUND = "not_found"
    TO_MUCH_PARENTS = "to_much_parents"
    JAR_NOT_FOUND = "jar_not_found"

    def __init__(self, code: str, version: str):
        super().__init__(code)
        self.version = version


class JvmLoadingError(BaseError):
    UNSUPPORTED_ARCH = "unsupported_arch"
    UNSUPPORTED_VERSION = "unsupported_version"


class DownloadError(Exception):

    CONN_ERROR = "conn_error"
    NOT_FOUND = "not_found"
    INVALID_SIZE = "invalid_size"
    INVALID_SHA1 = "invalid_sha1"

    def __init__(self, fails: Dict[str, str]):
        super().__init__()
        self.fails = fails


def json_request(url: str, method: str, *,
                 data: Optional[bytes] = None,
                 headers: Optional[dict] = None,
                 ignore_error: bool = False,
                 timeout: Optional[float] = None) -> Tuple[int, dict]:

    if headers is None:
        headers = {}
    if "Accept" not in headers:
        headers["Accept"] = "application/json"

    try:
        req = UrlRequest(url, data, headers, method=method)
        res: HTTPResponse = url_request.urlopen(req, timeout=timeout)
    except HTTPError as err:
        res = cast(HTTPResponse, err)

    try:
        data = res.read()
        return res.status, json.loads(data)
    except JSONDecodeError:
        if ignore_error:
            return res.status, {"raw": data}
        else:
            raise JsonRequestError(JsonRequestError.INVALID_RESPONSE_NOT_JSON, str(res.status))


def json_simple_request(url: str, *, ignore_error: bool = False, timeout: Optional[int] = None) -> dict:
    """ Make a GET request for a JSON API at specified URL. Might raise `JsonRequestError` if failed. """
    return json_request(url, "GET", ignore_error=ignore_error, timeout=timeout)[1]


def merge_dict(dst: dict, other: dict):

    for k, _ in other.items():
        if k in dst:
            if isinstance(dst[k], dict) and isinstance(other[k], dict):
                merge_dict(dst[k], other[k])
            elif isinstance(dst[k], list) and isinstance(other[k], list):
                dst[k].extend(other[k])
        else:
            dst[k] = other[k]


def interpret_rule_os(rule_os: dict) -> bool:
    os_name = rule_os.get("name")
    if os_name is None or os_name == get_minecraft_os():
        os_arch = rule_os.get("arch")
        if os_arch is None or os_arch == get_minecraft_arch():
            os_version = rule_os.get("version")
            if os_version is None or re.search(os_version, platform.version()) is not None:
                return True
    return False


def interpret_rule(rules: List[dict], features: Optional[dict] = None) -> bool:
    allowed = False
    for rule in rules:
        rule_os = rule.get("os")
        if rule_os is not None and not interpret_rule_os(rule_os):
            continue
        rule_features: Optional[dict] = rule.get("features")
        if rule_features is not None:
            feat_valid = True
            for feat_name, feat_expected in rule_features.items():
                if features.get(feat_name) != feat_expected:
                    feat_valid = False
                    break
            if not feat_valid:
                continue
        allowed = (rule["action"] == "allow")
    return allowed


def interpret_args(args: list, features: dict, dst: List[str]):
    for arg in args:
        if isinstance(arg, str):
            dst.append(arg)
        else:
            rules = arg.get("rules")
            if rules is not None:
                if not interpret_rule(rules, features):
                    continue
            arg_value = arg["value"]
            if isinstance(arg_value, list):
                dst.extend(arg_value)
            elif isinstance(arg_value, str):
                dst.append(arg_value)


def replace_vars(txt: str, replacements: Dict[str, str]) -> str:
    return txt.replace("${", "{").format_map(replacements)


def replace_list_vars(lst: List[str], replacements: Dict[str, str]) -> Generator[str, None, None]:
    return (replace_vars(elt, replacements) for elt in lst)


def get_minecraft_dir() -> str:
    home = path.expanduser("~")
    return {
        "Linux": path.join(home, ".minecraft"),
        "Windows": path.join(home, "AppData", "Roaming", ".minecraft"),
        "Darwin": path.join(home, "Library", "Application Support", "minecraft")
    }.get(platform.system())


_minecraft_os: Optional[str] = None
def get_minecraft_os() -> str:
    """ Return the current OS identifier used in rules matching, 'linux', 'windows', 'osx' and '' if not found. """
    global _minecraft_os
    if _minecraft_os is None:
        _minecraft_os = {"Linux": "linux", "Windows": "windows", "Darwin": "osx"}.get(platform.system(), "")
    return _minecraft_os


_minecraft_arch: Optional[str] = None
def get_minecraft_arch() -> str:
    """ Return the architecture to use in rules matching, 'x86', 'x86_64' or '' if not found. """
    global _minecraft_arch
    if _minecraft_arch is None:
        machine = platform.machine().lower()
        _minecraft_arch = "x86" if machine in ("i386", "i686") else "x86_64" if machine in ("x86_64", "amd64", "ia64") else ""
    return _minecraft_arch


_minecraft_archbits: Optional[str] = None
def get_minecraft_archbits() -> str:
    """ Return the address size of the architecture used for rules matching, '64', '32', or '' if not found. """
    global _minecraft_archbits
    if _minecraft_archbits is None:
        raw_bits = platform.architecture()[0]
        _minecraft_archbits = "64" if raw_bits == "64bit" else "32" if raw_bits == "32bit" else ""
    return _minecraft_archbits


_minecraft_jvm_os: Optional[str] = None
def get_minecraft_jvm_os() -> str:
    """ Return the OS identifier used to choose the right JVM to download. """
    global _minecraft_jvm_os
    if _minecraft_jvm_os is None:
        _minecraft_jvm_os = {
            "osx": {"x86": "mac-os"},
            "linux": {"x86": "linux-i386", "x86_64": "linux"},
            "windows": {"x86": "windows-x86", "x86_64": "windows-x64"}
        }.get(get_minecraft_os(), {}).get(get_minecraft_arch())
    return _minecraft_jvm_os


def can_extract_native(filename: str) -> bool:
    """ Return True if a file should be extracted to binaries directory. """
    return not filename.startswith("META-INF") and not filename.endswith(".git") and not filename.endswith(".sha1")


LEGACY_JVM_ARGUMENTS = [
    {
        "rules": [{"action": "allow", "os": {"name": "osx"}}],
        "value": ["-XstartOnFirstThread"]
    },
    {
        "rules": [{"action": "allow", "os": {"name": "windows"}}],
        "value": "-XX:HeapDumpPath=MojangTricksIntelDriversForPerformance_javaw.exe_minecraft.exe.heapdump"
    },
    {
        "rules": [{"action": "allow", "os": {"name": "windows", "version": "^10\\."}}],
        "value": ["-Dos.name=Windows 10", "-Dos.version=10.0"]
    },
    "-Djava.library.path=${natives_directory}",
    "-Dminecraft.launcher.brand=${launcher_name}",
    "-Dminecraft.launcher.version=${launcher_version}",
    "-cp",
    "${classpath}"
]


if __name__ == "__main__":
    
    EXIT_OK = 0
    EXIT_FAILURE = 1
    EXIT_WRONG_USAGE = 9
    EXIT_VERSION_NOT_FOUND = 10
    EXIT_DOWNLOAD_ERROR = 13
    EXIT_AUTH_ERROR = 14
    EXIT_DEPRECATED_ARGUMENT = 16
    EXIT_JSON_REQUEST_ERROR = 18
    EXIT_JVM_LOADING_ERROR = 19
    
    JVM_ARGS_DEFAULT = ["-Xmx2G",
                       "-XX:+UnlockExperimentalVMOptions",
                       "-XX:+UseG1GC",
                       "-XX:G1NewSizePercent=20",
                       "-XX:G1ReservePercent=20",
                       "-XX:MaxGCPauseMillis=50",
                       "-XX:G1HeapRegionSize=32M"]
    
    
    class CliContext(Context):
        def __init__(self, ns: Namespace):
            super().__init__(ns.main_dir, ns.work_dir)
            self.ns = ns
    
    def main(args: Optional[List[str]] = None):
    
        # load_addons()
    
        parser = register_arguments()
        ns = parser.parse_args(args or sys.argv[1:])

        while True:
            cmd_start(ns, CliContext(ns))

            sys.exit(EXIT_OK)

    # CLI Parser
    
    def register_arguments() -> ArgumentParser:

        parser = ArgumentParser(allow_abbrev=False, prog="launcher")
        parser.add_argument("--main-dir")
        parser.add_argument("--work-dir")
        parser.add_argument("--dry", action="store_true")
        parser.add_argument("--disable-mp", action="store_true")
        parser.add_argument("--disable-chat", action="store_true")
        parser.add_argument("--demo", action="store_true")
        parser.add_argument("--jvm")
        parser.add_argument("--jvm-args")
        parser.add_argument("-u", "--username", metavar="NAME")
        parser.add_argument("-i", "--uuid")
        parser.add_argument("-s", "--server")
        parser.add_argument("-p", "--server-port", type=int, metavar="PORT")
        parser.add_argument("version", nargs="?", default="release")
        return parser
    
    def cmd_start(ns: Namespace, ctx: CliContext):
    
        manifest = VersionManifest.load_from_url()

        version_id, _ = manifest.filter_latest(ns.version)
        version = Version(ctx, version_id)
        version.manifest = manifest

        version.prepare_meta()

        version.prepare_jar()

        version.prepare_assets()

        version.prepare_logger()
        print(f"Downloading version: {ns.version}")
        version.dl.add_callback(lambda: print("Done downloading!"))
        version.prepare_libraries()
        # libs_count = len(version.classpath_libs) + len(version.native_libs)
        if ns.jvm is None:
            version.prepare_jvm()
        version.dl.download_files()
        version.dl.reset()

        if ns.dry:
            return

        start_opts = StartOptions()
        start_opts.disable_multiplayer = ns.disable_mp
        start_opts.disable_chat = ns.disable_chat
        start_opts.demo = ns.demo
        start_opts.server_address = ns.server
        start_opts.server_port = ns.server_port
        start_opts.jvm_exec = ns.jvm

        start_opts.uuid = ns.uuid
        start_opts.username = ns.username
        start = Start(version)
        start.prepare(start_opts)
        start.jvm_args.extend(JVM_ARGS_DEFAULT if ns.jvm_args is None else ns.jvm_args.split())

        start.start()

        sys.exit(EXIT_OK)
    

    main()

