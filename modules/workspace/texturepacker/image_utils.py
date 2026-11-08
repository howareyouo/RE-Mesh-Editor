import numpy as np
import os
def NRMRToNRRX(NRMRArray):
    NRMRArray = np.asarray(NRMRArray, dtype=np.float32)
    #Result is a bit brighter than the original nrrx but it's close enough that I don't think it matters much
    x = NRMRArray[0::4]
    y = NRMRArray[1::4]
    r = NRMRArray[3::4]

    x = x * 2.0 - 1.0
    y = y * 2.0 - 1.0

    cos_a = np.cos(np.deg2rad(-45))
    sin_a = np.sin(np.deg2rad(-45))

    nr_x = x * cos_a - y * sin_a
    nr_y = x * sin_a + y * cos_a

    nr_x = np.sign(nr_x) * np.sqrt(np.abs(nr_x))
    nr_y = np.sign(nr_y) * np.sqrt(np.abs(nr_y))

    nr_x = (nr_x + 1.0) * 0.5
    nr_y = (nr_y + 1.0) * 0.5

    np.clip(nr_x, 0.0, 1.0, out=nr_x)
    np.clip(nr_y, 0.0, 1.0, out=nr_y)

    NRRXArray = np.empty_like(NRMRArray)

    NRRXArray[0::4] = r
    NRRXArray[1::4] = nr_x
    NRRXArray[2::4] = 1.0
    NRRXArray[3::4] = nr_y

    return NRRXArray


# (name suffix, is_data, colorspace, alpha_mode, RGBA fill, print label)
_PLACEHOLDER_SPECS = [
    ("BaseColor", False, "sRGB",      "CHANNEL_PACKED", (1.0, 1.0, 1.0, 1.0), "Base Color"),
    ("Normal",    True,  "Non-Color", None,             (0.5, 0.5, 1.0, 1.0), "Normal"),
    ("Roughness", True,  "Non-Color", None,             (1.0, 1.0, 1.0, 1.0), "Roughness"),
    ("Emissive",  True,  "Non-Color", None,             (0.0, 0.0, 0.0, 0.0), "Emissive"),
    ("AO",        True,  "Non-Color", None,             (1.0, 1.0, 1.0, 1.0), "Ambient Occlusion"),
    ("Metallic",  True,  "Non-Color", None,             (0.0, 0.0, 0.0, 0.0), "Metallic"),
    ("Opacity",   True,  "Non-Color", None,             (1.0, 1.0, 1.0, 1.0), "Alpha"),
]


def _preparePlaceholderImage(imageName, imageXRes, imageYRes, isData, colorspace, alphaMode,
                             useExistingBakes, bakeDir, fileExtension):
    """Load an existing bake or create a blank image datablock.

    Returns (img, generated) - generated is False when an existing bake was loaded.
    """
    import bpy
    bakePath = os.path.join(bakeDir, imageName + fileExtension)
    if useExistingBakes and os.path.isfile(bakePath):
        img = bpy.data.images.load(bakePath, check_existing=True)
        img.name = imageName
        print(f"\tLoaded existing bake for {imageName}")
        generated = False
    else:
        img = bpy.data.images.new(imageName, imageXRes, imageYRes, is_data=isData)
        generated = True
    img.colorspace_settings.name = colorspace
    if alphaMode is not None:
        img.alpha_mode = alphaMode
    return img, generated


def generatePlaceholderMaps(textureSetName,imageXRes,imageYRes,useExistingBakes,bakeDir,fileExtension):
    import bpy
    print(f"Texture Set: {textureSetName}")
    print(f"Resolution: {imageXRes}x{imageYRes}\n")
    print("Generating Placeholder Textures...")

    for suffix, isData, colorspace, alphaMode, rgba, label in _PLACEHOLDER_SPECS:
        imageName = f"{textureSetName}_{suffix}"
        img, generated = _preparePlaceholderImage(imageName, imageXRes, imageYRes, isData,
                                                  colorspace, alphaMode,
                                                  useExistingBakes, bakeDir, fileExtension)
        if not generated:
            continue
        print(f"Generating Blank {label}...")
        outPixelArray = np.empty(shape=imageXRes * imageYRes * 4, dtype=np.float32)
        outPixelArray[0::4] = rgba[0]
        outPixelArray[1::4] = rgba[1]
        outPixelArray[2::4] = rgba[2]
        outPixelArray[3::4] = rgba[3]
        img.pixels.foreach_set(outPixelArray)
