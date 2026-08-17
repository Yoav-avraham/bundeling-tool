import os
import argparse
import requests
import json
from datetime import datetime
import tomllib
import tarfile
import shutil
import subprocess
import stat

COMMIT_SHA = os.getenv("GITHUB_SHA") or "example-sha"
GIT_URL = "https://github.com/Yoav-avraham/bundeling-tool.git"

def create_parser():
    """
        The function creates parsers for the modes and flags of the cli tool
    """
    
    # main parser
    parser = argparse.ArgumentParser(description="Package bundling tool")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # export parser
    export_parser = subparsers.add_parser("export", help="Export project dependencies")
    export_parser.add_argument("project_path", help="Path to project directory")
    export_parser.add_argument("--no-cleanup", action="store_true", help="Keep extracted dependency folders after creating archives")
    export_parser.add_argument("--commit-sha" , help="Commit SHA to compare current dependencies against")
    export_parser.add_argument("--latest-commit", action="store_true", help="Get the latest Commit SHA to compare current dependencies against")
    
    # import parser
    import_parser = subparsers.add_parser("import", help="Import bundle archive")
    import_parser.add_argument("bundle_file",help="Path to bundle tar.gz")

    return parser


def validate_project_path(project_path: str):
    """
        The function validates the path of the project for export 
    """
    
    # checks if the path exists 
    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Path does not exist: {project_path}")

    # checks if the path is not a dir
    if not os.path.isdir(project_path):
        raise NotADirectoryError(f"Path is not a directory: {project_path}")

    return project_path


def find_lock_files(project_path: str):
    """
        The function finds all of the lock files in the project
        and returns their paths
    """
    
    # init lists for uv and npm locks paths
    uv_lock_files = []
    npm_lock_files = []

    # recursive walk in the project dir in search of lock files
    for root, dirs, files in os.walk(project_path):

        # check each file 
        for file in files:

            # if file is package-lock or uv.lock append to list
            if file == "package-lock.json":
                npm_lock_files.append(os.path.join(root, file))

            elif file == "uv.lock":
                uv_lock_files.append(os.path.join(root, file))

    return npm_lock_files, uv_lock_files


def extract_npm_dependencies(lock_path: str):
    """
        The function takes the npm dependencies , their versions ,
        integrity and url
    """

    # init list for the dependencies
    dependencies = []
    dependencies_set = set()

    try:
        # open the json file and load it into a dict
        with open(lock_path, "r", encoding="utf-8") as file:
            lock_data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Failed to read npm lock files: {lock_path}"
        ) from error

    # check each item in the dict
    for path, info in lock_data["packages"].items():

        # if the item doesnt start with node_modules skip
        if "node_modules/" not in path:
            continue

        # split the name from the path
        package_name = path.split("node_modules/")[-1]

        # append the dependencies to the list
        dependencies.append({
            "name": package_name,
            "version": info.get("version"),
            "resolved": info.get("resolved"),
            "integrity": info.get("integrity")
        })
        
        dependencies_set.add(f"{package_name}@{info.get("version")}")

    return dependencies , dependencies_set


def extract_uv_dependencies(lock_path: str):
    """
        The function extracts uv dependencies from the lock file
    """
    
    # init list for dependencies
    dependencies = []
    dependencies_set = set()
    try:  

        # open the uv.lock file and load it into a dict
        with open(lock_path, "rb") as file:
            lock_data = tomllib.load(file)
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise RuntimeError(
            f"Failed to read uv lock files {lock_path}"
        ) from error

    # check each item in the dict
    for package in lock_data.get("package", []):

        # take the current package 
        package_data = {
            "name": package.get("name"),
            "version": package.get("version"),
            "wheels": package.get("wheels", []),
            "sdist": package.get("sdist")
        }

        # append to the dependencies list
        dependencies.append(package_data)
        dependencies_set.add(f"{package.get("name")}@{package.get("version")}")

    return dependencies , dependencies_set


def download_npm_dependencies(dependencies, output_dir, exists_set = None):
    """
        The function download the npm dependencies from the dependencies list
    """
    
    if exists_set is None: 
        exists_set=set()

    # create dir for the dependencies
    os.makedirs(output_dir, exist_ok=True)

    # try to install every dependency 
    for dependency in dependencies:

        # get the resolved url in order to install the dependency
        url = dependency.get("resolved")

        # if there isnt url skip
        if not url:
            print(f"Skipping {dependency['name']} - no URL")
            continue
        
        package = f"{dependency["name"]}@{dependency["version"]}"
        
        if package in exists_set:
            print(f"Skipping {package} already exists")
            continue
        
        # create path for dependency package file
        filename = (dependency["name"].replace("/", "_") + "-" + dependency["version"] + ".tgz")
        file_path = os.path.join(output_dir, filename)
        try:
            # send request in order to download and raise error if status is bad
            response = requests.get(url, timeout=30)
            response.raise_for_status()
        except requests.RequestException as error:
            raise RuntimeError(
                f"Failed to download dependency from {url}"
                ) from error

        # create the file and write the dependency
        with open(file_path, "wb") as file:
            file.write(response.content)

        # print what package was downloaded
        print(f"Downloaded: {filename}")
        
        exists_set.add(package)


def download_uv_dependencies(dependencies, output_dir, exists_set=None):
    """
        The function download the uv dependencies from the dependencies list
    """
    if exists_set is None:
        exists_set=set()
    
    # create dir for the dependencies
    os.makedirs(output_dir, exist_ok=True)

    # try to install every dependency
    for dependency in dependencies:
        
        package = f"{dependency["name"]}@{dependency["version"]}"
        
        if package in exists_set:
            print(f"Skipping: {package} - already downloaded")
            continue
        
        # for every wheel file in the wheels list try to installs
        for wheel in dependency.get("wheels", []):

            # get the url
            url = wheel.get("url")
            
            # create the filename from the path of the url
            filename = os.path.basename(url)
            file_path = os.path.join(output_dir, filename)
            
            # skip if there is no url
            if not url:
                print("Skippng - no url")
                continue
            
            try:
                # send request to download the dependency from the url
                # raise error if bad status
                response = requests.get(url, timeout=30)
                response.raise_for_status()
            except requests.RequestException as error:
                raise RuntimeError(
                    f"Failed to download dependency from {url}"
                    ) from error

            # create the dependency file
            with open(file_path, "wb") as file:
                file.write(response.content)

            # print what dependency was downloaded
            print(f"Downloaded: {filename}")

        # get the sdist url if there is one
        sdist = dependency.get("sdist")

        # if there is sdist url
        if sdist:
            
            # take the url check if theres one and download if so
            url = sdist.get("url")
            if url:
                
                # create the filename from the path of the url
                filename = os.path.basename(url)
                file_path = os.path.join(output_dir, filename)
                
                # check if dependency already exists
                if os.path.exists(file_path):
                            print(f"Skipping: {filename} - already downloaded")
                            continue
                
                try:
                    
                    # send request to download the dependency from the url
                    # raise error if bad status 
                    response = requests.get(url, timeout=30)
                    response.raise_for_status()
                except requests.RequestException as error:
                    raise RuntimeError(
                        f"Failed to download dependency from {url}"
                    ) from error

                # create the dependency file
                with open(file_path, "wb") as file:
                    file.write(response.content)
                    
                # print what dependency was downloaded
                print(f"Downloaded: {filename}")

def gather_npm_dependencies_from_locks(lock_files):
    """
        The function takes a list of npm lock files and gather all of
        the dependencies into a list and set
    """
    
    npm_dependencies = []
    npm_set = set()
    
    # gather the dependencies for each npm lockfile
    for lock_file in lock_files:
    
        # print message and extract dependencies
        print(f"Processing npm: {lock_file}")
        dependencies, dependencies_set = extract_npm_dependencies(lock_file)
        
        # add the dependencies to the set and list
        npm_dependencies.extend(dependencies)
        npm_set.update(dependencies_set)
        
    return npm_dependencies, npm_set

def gather_uv_dependencies_from_lockfiles(lock_files):
    """
        The function takes a list of uv lock files and gather all of
        the dependencies into a list and set
    """
    
    uv_dependencies = []
    uv_set = set()
    
    for lock_file in lock_files:
        
        # print message and extract dependencies
        print(f"Processing uv: {lock_file}")
        dependencies, dependencies_set = extract_uv_dependencies(lock_file)
        
        # add the dependencies to the set and list
        uv_dependencies.extend(dependencies)
        uv_set.update(dependencies_set)
        
    return uv_dependencies, uv_set
        
       

def create_bundle_archive(source_dir: str, output_file: str):
    """
        The function takes the directory that contains
        the dependencies and zip it into a bundle
    """
    try:
        # create a gz gile
        with tarfile.open(output_file, "w:gz") as tar:
            # add the dir 
            tar.add(source_dir, arcname=os.path.basename(source_dir))
    except (tarfile.TarError, OSError) as error:
        raise RuntimeError(
            f"Failed to create bundle archive: {source_dir}"
        ) from error
        
    print(f"Bundle created: {output_file}")


def create_dependencies_archive(source_dir: str, output_file: str):
    """
        The function creates an archive of every dependency
    """
    try:
        # create a new gz fie and add all of the files in the dir
        with tarfile.open(output_file, "w:gz") as tar:
            for file in os.listdir(source_dir):
                file_path = os.path.join(source_dir, file)
                tar.add(file_path, arcname=file)
    except (tarfile.TarError, OSError) as error:
        raise RuntimeError(
            f"Failed to create dependencies archive: {source_dir}"
        ) from error
        
    print(f"Dependencies archive created: {output_file}")


def extract_bundle_archive(bundle_file: str, output_dir: str):
    """
        The function unzip a bundle
    """
    
    # validates the bundle's path
    if not os.path.exists(bundle_file):
        raise FileNotFoundError(f"Bundle file does not exist: {bundle_file}")

    # create dir for the bundle content
    os.makedirs(output_dir, exist_ok=True)
    
    try:
        # open the gz file and extract into the dir
        with tarfile.open(bundle_file, "r:gz") as tar:
            
            tar.extractall(path=output_dir)
    except (tarfile.TarError, OSError) as error:
        raise RuntimeError(
            f"Failed to extract bundle: {bundle_file}"
        ) from error

    print(f"Bundle extracted to: {output_dir}")


def export_project(project_path, no_cleanup=False, commit_sha=None, latest_commit=False):
    """
        The function handles the export command from getting the dependencies
        from the lockfiles to downloading it and creating a bundle
    """
    
    # check if the projects path exists or a dir
    project_path = validate_project_path(project_path)

    # get all the paths of the lock files
    npm_lock_files, uv_lock_files = find_lock_files(project_path)

    # if theres no lock files stop
    if not npm_lock_files and not uv_lock_files:
        print("No lock files found")
        return

    # get the timestamp for the file name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_dir = os.path.join(".", timestamp)

    # make paths for seperate dirs for uv and npm
    npm_dir = os.path.join(bundle_dir, "npm")
    uv_dir = os.path.join(bundle_dir, "uv")

    # gather all of the dependencies from lockfiles
    npm_dependencies, npm_set = gather_npm_dependencies_from_locks(npm_lock_files)
    uv_dependencies, uv_set = gather_uv_dependencies_from_lockfiles(uv_lock_files)
    
    if commit_sha or latest_commit:
        
        # clone the git repo from a specific sha
        git_path = 'remote_git'
        
        # check if theres already previous git repo
        if os.path.exists(git_path):
            shutil.rmtree(git_path, onexc=remove_readonly)
        
        try:
            
            # clone the git repo of the specific sha
            fetch_git_by_sha(GIT_URL, commit_sha, latest_commit)
            
            # get lock files
            git_npm_lock_files, git_uv_lock_files = find_lock_files(git_path)
            
            # gather all of the dependencies in 
            git_npm_dependencies, git_npm_set = gather_npm_dependencies_from_locks(git_npm_lock_files)
            git_uv_dependencies, git_uv_set = gather_uv_dependencies_from_lockfiles(git_uv_lock_files)
            
            # download the dependencies
            download_npm_dependencies(npm_dependencies, npm_dir, git_npm_set)
            download_uv_dependencies(uv_dependencies, uv_dir, git_uv_set)   
        finally:
            if os.path.exists(git_path):
                shutil.rmtree(git_path, onexc=remove_readonly)
    else:
        # download all of the dependencies
        download_npm_dependencies(npm_dependencies, npm_dir)
        download_uv_dependencies(uv_dependencies, uv_dir)
    
    # if theres no uv dependencies remove the dir
    if len(os.listdir(uv_dir)) == 0:
        print("No uv dependencies required download")
        os.rmdir(uv_dir)
    
    #  if theres no npm dependencies remove the dir
    if len(os.listdir(npm_dir)) == 0:
        print("No npm dependencies required download")
        os.rmdir(npm_dir)
    else:
        # create npm-dependencies.tgz
        npm_archive = os.path.join(bundle_dir, "npm-dependencies.tgz")
            
        create_dependencies_archive(npm_dir, npm_archive)
        
        # remove original npm dir
        shutil.rmtree(npm_dir)
            
    if len(os.listdir(bundle_dir)):
    
        # create final bundle
        create_bundle_archive(bundle_dir, f"{timestamp}-{COMMIT_SHA}.tar.gz")
        
    else:
        print("No dependencies required download")

    # cleanup the extracted folder
    if not no_cleanup:
        shutil.rmtree(bundle_dir)
        

def import_bundle(bundle_file):
    """
        The function handles import of a zipped bundle file
    """
    
    extract_bundle_archive(bundle_file, "imported_bundle")
    
def fetch_git_by_sha(git_url, commit_sha, latest_commit=False):
    """
        The function clones a repo from a git url and specific commit
    """
    
    # name for the dirs and archive
    git_path = "remote_git"
    temp_git = "temp_git"
    repo_archive = "repo.tar"   

    try:
        # clone the repo and fetch the specific commit as an archive
        subprocess.run(["git", "clone", "--no-checkout", git_url, temp_git],check=True)
        
        commit_sha = "HEAD^" if latest_commit else commit_sha
        
        print(f"Processing the commit: {commit_sha}")
        
        subprocess.run(["git", "-C", "temp_git", "archive", "--format=tar", commit_sha, "-o", f"../{repo_archive}"],check=True)

        # make dir for the unzipped archive
        os.makedirs(git_path, exist_ok=True)

        # open the repo archive 
        with tarfile.open(repo_archive, "r") as tar:
            tar.extractall(git_path)   
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"Failed to fetch git commit {commit_sha}") from error
    finally:
        # remove the archive if it exists
        if os.path.exists(repo_archive):
            os.remove(repo_archive)

        # remove the temporary git repo if it exists
        if os.path.exists(temp_git):
            shutil.rmtree(temp_git, onexc=remove_readonly)


def remove_readonly(func, path, exc):
    """
        The function helps removing a full dir
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def main():

    # creates general parser and get the args
    parser = create_parser()
    args = parser.parse_args()
    try:
        # if the command is export use the export function
        if args.command == "export":

            export_project(args.project_path, args.no_cleanup, args.commit_sha, args.latest_commit)

        # if the function is import use the import function
        elif args.command == "import":
            
            import_bundle(args.bundle_file)
    except (FileNotFoundError, NotADirectoryError, RuntimeError) as error:
        print(f"error: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()