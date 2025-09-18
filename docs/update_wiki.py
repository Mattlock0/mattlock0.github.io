# used to update files in wiki only with documentation that is ready

from pathlib import Path
import shutil

ONE_DRIVE_BASE = Path('C:/Users/visio/OneDrive/Documents/Obsidian Notes/Hearth/')
WIKI_LOCATION = Path('wiki/').absolute()
FOLDER_LIST = [Path('Character'), Path('Faction'), Path('Geography'), Path('Politics & Tradition'),
               Path('Species'), Path('Technology')]

if __name__ == '__main__':
    if not ONE_DRIVE_BASE.exists() or not WIKI_LOCATION.exists():
        print(f'ERROR: Locations misconfigured!')
        exit
    
    # now that we know those locations exist, parse through them!
    for folder in FOLDER_LIST:
        if not (ONE_DRIVE_BASE / folder).exists():
            print(f'Specified folder {folder} does not exist!')
            exit
        
        output_location = WIKI_LOCATION / folder
        if output_location.exists():
            print(f'Wiki folder {folder.stem} already exists--removing.')
            shutil.rmtree(output_location)
            # currently has no access, so you'll have to chmod the folder first
        
        try:
            print(f'Copying files from {folder.stem} folder to {output_location}')
            shutil.copytree(ONE_DRIVE_BASE / folder, output_location)
        except Exception as e:
            print(f'An error occured: {e}')
