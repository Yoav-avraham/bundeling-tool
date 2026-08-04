import os
import argparse
import requests
import json
from datetime import datetime
import tomllib
import tarfile


def create_parser():

    parser = argparse.ArgumentParser(description="Package bundling tool")

    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser("export", help="Export project dependencies")

    export_parser.add_argument("project_path", help="Path to project directory")

    import_parser = subparsers.add_parser("import", help="Import bundle archive")

    import_parser.add_argument("bundle_file",help="Path to bundle tar.gz")

    return parser


def validate_project_path(project_path: str):

    if not os.path.exists(project_path):
        raise FileNotFoundError(f"Path does not exist: {project_path}")

    if not os.path.isdir(project_path):
        raise NotADirectoryError(f"Path is not a directory: {project_path}")

    return project_path


def find_lock_files(project_path: str):

    uv_lock_files = []
    npm_lock_files = []

    for root, dirs, files in os.walk(project_path):

        for file in files:

            if file == "package-lock.json":
                npm_lock_files.append(os.path.join(root, file))

            elif file == "uv.lock":
                uv_lock_files.append(os.path.join(root, file))

    return npm_lock_files, uv_lock_files


def extract_npm_dependencies(lock_path: str):

    dependencies = []

    with open(lock_path, "r", encoding="utf-8") as file:
        lock_data = json.load(file)


    for path, info in lock_data["packages"].items():

        if "node_modules/" not in path:
            continue


        package_name = path.split("node_modules/")[-1]

        dependencies.append({
            "name": package_name,
            "version": info.get("version"),
            "resolved": info.get("resolved"),
            "integrity": info.get("integrity")
        })

    return dependencies



def extract_uv_dependencies(lock_path: str):

    dependencies = []

    with open(lock_path, "rb") as file:
        lock_data = tomllib.load(file)

    for package in lock_data.get("package", []):

        package_data = {
            "name": package.get("name"),
            "version": package.get("version"),
            "wheels": package.get("wheels", []),
            "sdist": package.get("sdist")
        }

        dependencies.append(package_data)

    return dependencies



def download_npm_dependencies(dependencies, output_dir):

    os.makedirs(output_dir, exist_ok=True)

    for dependency in dependencies:

        url = dependency.get("resolved")

        if not url:
            print(f"Skipping {dependency['name']} - no URL")
            continue

        response = requests.get(url)

        response.raise_for_status()

        filename = (dependency["name"].replace("/", "_") + "-" + dependency["version"] + ".tgz")

        file_path = os.path.join(output_dir, filename)

        with open(file_path, "wb") as file:
            file.write(response.content)

        print(f"Downloaded: {filename}")



def download_uv_dependencies(dependencies, output_dir):

    os.makedirs(output_dir, exist_ok=True)

    for dependency in dependencies:

        for wheel in dependency.get("wheels", []):

            url = wheel.get("url")

            if not url:
                continue

            response = requests.get(url)

            response.raise_for_status()

            filename = os.path.basename(url)

            file_path = os.path.join(output_dir, filename)

            with open(file_path, "wb") as file:
                file.write(response.content)

            print(f"Downloaded: {filename}")


        sdist = dependency.get("sdist")

        if sdist:

            url = sdist.get("url")

            if url:

                response = requests.get(url)

                response.raise_for_status()

                filename = os.path.basename(url)

                file_path = os.path.join(output_dir, filename)

                with open(file_path, "wb") as file:
                    file.write(response.content)

                print(f"Downloaded: {filename}")



def create_bundle_archive(source_dir: str, output_file: str):

    with tarfile.open(output_file, "w:gz") as tar:

        tar.add(source_dir, arcname=os.path.basename(source_dir))

    print(f"Bundle created: {output_file}")



def extract_bundle_archive(bundle_file: str, output_dir: str):

    if not os.path.exists(bundle_file):
        raise FileNotFoundError(f"Bundle file does not exist: {bundle_file}")

    os.makedirs(output_dir, exist_ok=True)

    with tarfile.open(bundle_file, "r:gz") as tar:
        
        tar.extractall(path=output_dir)

    print(f"Bundle extracted to: {output_dir}")


def export_project(project_path):

    project_path = validate_project_path(project_path)

    npm_lock_files, uv_lock_files = find_lock_files(project_path)

    if not npm_lock_files and not uv_lock_files:
        print("No lock files found")
        return

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    bundle_dir = os.path.join("bundled_packages", timestamp)

    npm_dir = os.path.join(bundle_dir, "npm")

    uv_dir = os.path.join(bundle_dir, "uv")

    for lock_file in npm_lock_files:

        print(f"Processing npm: {lock_file}")

        dependencies = extract_npm_dependencies(lock_file)

        download_npm_dependencies(dependencies, npm_dir)

    for lock_file in uv_lock_files:

        print(f"Processing uv: {lock_file}")

        dependencies = extract_uv_dependencies(lock_file)

        download_uv_dependencies(dependencies, uv_dir)

    create_bundle_archive(
        bundle_dir,
        f"{timestamp}.tar.gz"
    )

def import_bundle(bundle_file):

    extract_bundle_archive(bundle_file, "imported_bundle")

def main():

    parser = create_parser()

    args = parser.parse_args()


    if args.command == "export":

        export_project(args.project_path)


    elif args.command == "import":

        import_bundle(args.bundle_file)



if __name__ == "__main__":
    main()