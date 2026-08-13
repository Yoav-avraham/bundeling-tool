import os
import argparse
import requests
import json
from datetime import datetime
import tomllib
import tarfile
import shutil

COMMIT_SHA = os.getenv("GITHUB_SHA") or "example-sha"


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

    # open the json file and load it into a dict
    with open(lock_path, "r", encoding="utf-8") as file:
        lock_data = json.load(file)

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

    # open the uv.lock file and load it into a dict
    with open(lock_path, "rb") as file:
        lock_data = tomllib.load(file)

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


def download_npm_dependencies(dependencies, output_dir, exists_set = set()):
    """
        The function download the npm dependencies from the dependencies list
    """

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
        
        # send request in order to download and raise error if status is bad
        response = requests.get(url)
        response.raise_for_status()

        # create the file and write the dependency
        with open(file_path, "wb") as file:
            file.write(response.content)

        # print what package was downloaded
        print(f"Downloaded: {filename}")
        
        exists_set.add(package)


def download_uv_dependencies(dependencies, output_dir, exists_set=set()):
    """
        The function download the uv dependencies from the dependencies list
    """
    
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
            
            # send request to download the dependency from the url
            # raise error if bad status
            response = requests.get(url)
            response.raise_for_status()

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
                
                # send request to download the dependency from the url
                # raise error if bad status 
                response = requests.get(url)
                response.raise_for_status()

                # create the dependency file
                with open(file_path, "wb") as file:
                    file.write(response.content)
                    
                # print what dependency was downloaded
                print(f"Downloaded: {filename}")


def create_bundle_archive(source_dir: str, output_file: str):
    """
        The function takes the directory that contains
        the dependencies and zip it into a bundle
    """
    
    # create a gz gile
    with tarfile.open(output_file, "w:gz") as tar:

        # add the dir 
        tar.add(source_dir, arcname=os.path.basename(source_dir))
        
    print(f"Bundle created: {output_file}")


def create_dependencies_archive(source_dir: str, output_file: str):
    """
        The function creates an archive of every dependency
    """
    
    # create a new gz fie and add all of the files in the dir
    with tarfile.open(output_file, "w:gz") as tar:
        for file in os.listdir(source_dir):
            file_path = os.path.join(source_dir, file)
            tar.add(file_path, arcname=file)

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
    
    # open the gz file and extract into the dir
    with tarfile.open(bundle_file, "r:gz") as tar:
        
        tar.extractall(path=output_dir)

    print(f"Bundle extracted to: {output_dir}")


def export_project(project_path, no_cleanup=False):
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

    npm_dependencies = []
    npm_set = set()
    
    uv_dependencies = []
    uv_set = set()
    
    # gather the dependencies for each npm lockfile
    for lock_file in npm_lock_files:

        print(f"Processing npm: {lock_file}")

        dependencies, dependencies_set = extract_npm_dependencies(lock_file)
        npm_dependencies.extend(dependencies)
        npm_set.update(dependencies_set)
    
    # download all   
    download_npm_dependencies(npm_dependencies, npm_dir)

    # download the dependencies for each uv lockfile
    for lock_file in uv_lock_files:

        print(f"Processing uv: {lock_file}")

        dependencies, dependencies_set = extract_uv_dependencies(lock_file)
        uv_dependencies.extend(dependencies)
        uv_set.update(dependencies_set)
    print(npm_set)
    print(uv_set)
    download_uv_dependencies(uv_dependencies, uv_dir, uv_set)

    # Create npm-dependencies.tgz
    npm_archive = os.path.join(bundle_dir, "npm-dependencies.tgz")

    if os.path.exists(npm_dir) and os.listdir(npm_dir):
        create_dependencies_archive(npm_dir, npm_archive)

        # remove original npm folder
        for file in os.listdir(npm_dir):
            os.remove(os.path.join(npm_dir, file))

        os.rmdir(npm_dir)

    # Create final bundle
    create_bundle_archive(bundle_dir, f"{timestamp}-{COMMIT_SHA}.tar.gz")

    # Cleanup the extracted folder
    if not no_cleanup:
        shutil.rmtree(bundle_dir)
        

def import_bundle(bundle_file):
    """
        The function handles import of a zipped bundle file
    """
    
    extract_bundle_archive(bundle_file, "imported_bundle")


def main():

    # creates general parser and get the args
    parser = create_parser()
    args = parser.parse_args()

    # if the command is export use the export function
    if args.command == "export":

        export_project(args.project_path, args.no_cleanup)

    # if the function is import use the import function
    elif args.command == "import":
        
        import_bundle(args.bundle_file)


if __name__ == "__main__":
    main()