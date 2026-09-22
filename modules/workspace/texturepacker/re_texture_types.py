import bpy
import numpy as np
from .image_utils import NRMRToNRRX,NRRXToNRMR
#-------------------
#Shared helpers for texture type packing.
#
#Source images are scaled at most once per call and their pixels are read at
#most once per source, instead of every pack function re-scaling and re-reading
#the same source (previously a 4K Normal map was scaled+foreach_get'd once per
#output type, so NROC+NRRT+NRRC+NRRO re-read the same 256MB buffer 4 times).
def _getSourceImage(textureSetName,imageXRes,imageYRes,sourceName):
    """Scale (only when needed) and return the source image datablock."""
    img = bpy.data.images[f"{textureSetName}_{sourceName}"]
    if (img.size[0] != imageXRes) or (img.size[1] != imageYRes):
        img.scale(imageXRes,imageYRes)
    return img

def _getSourcePixels(textureSetName,imageXRes,imageYRes,sourceName):
    """Return the RGBA pixels of a (once-scaled) source image; each source is
    read from Blender only once per (set, resolution) batch."""
    global _sourcePixelCache,_sourcePixelCacheKey
    key = (textureSetName,imageXRes,imageYRes)
    if _sourcePixelCacheKey != key:
        _sourcePixelCache.clear()
        _sourcePixelCacheKey = key
    cache = _sourcePixelCache
    arr = cache.get(sourceName)
    if arr is None:
        img = _getSourceImage(textureSetName,imageXRes,imageYRes,sourceName)
        arr = np.empty(imageXRes*imageYRes*4,dtype=np.float32)
        img.pixels.foreach_get(arr)
        cache[sourceName] = arr
    return arr

_sourcePixelCache = {}
_sourcePixelCacheKey = None

def _createOutImage(outImageName,imageXRes,imageYRes,isData,colorspace):
    outImg = bpy.data.images.new(outImageName,imageXRes,imageYRes,alpha = True,is_data = isData)
    outImg.alpha_mode = "CHANNEL_PACKED"
    outImg.colorspace_settings.name = colorspace
    return outImg

def _packRGBAlpha(textureSetName,imageXRes,imageYRes,outImageName,colorSource,alphaSpec):
    """Common RGB + packed-alpha pack. alphaSpec is a (sourceName, channel) tuple
    or a constant value; 'invert' wraps the tuple to output 1 - value."""
    colorArray = _getSourcePixels(textureSetName,imageXRes,imageYRes,colorSource)
    outPixelArray = np.copy(colorArray)
    if isinstance(alphaSpec,(int,float)):
        outPixelArray[3::4] = alphaSpec
    else:
        srcName, ch, invert = alphaSpec
        alphaArray = _getSourcePixels(textureSetName,imageXRes,imageYRes,srcName)
        if invert:
            outPixelArray[3::4] = 1.0 - alphaArray[ch::4]
        else:
            outPixelArray[3::4] = alphaArray[ch::4]
    return outPixelArray

def _packNRX(textureSetName,imageXRes,imageYRes,outImageName,alphaSource,bSource):
    """Normal-family pack: normal RGB -> NRRX, alpha slot from a source, B slot
    from a source or constant."""
    normalArray = np.copy(_getSourcePixels(textureSetName,imageXRes,imageYRes,"Normal"))#Copy: the alpha slot is mutated below and the cached buffer must stay pristine
    alphaArray = _getSourcePixels(textureSetName,imageXRes,imageYRes,alphaSource)
    normalArray[3::4] = alphaArray[0::4]
    outPixelArray = NRMRToNRRX(normalArray)
    if isinstance(bSource,(int,float)):
        outPixelArray[2::4] = bSource
    else:
        bArray = _getSourcePixels(textureSetName,imageXRes,imageYRes,bSource)
        outPixelArray[2::4] = bArray[0::4]
    outImg = _createOutImage(outImageName,imageXRes,imageYRes,True,"Non-Color")
    outImg.pixels.foreach_set(outPixelArray)
    return outImg

def _packChannelMix(textureSetName,imageXRes,imageYRes,outImageName,spec):
    """Output the given channel spec: a dict of 'rgba' -> (sourceName, channel) or const."""
    outPixelArray = np.empty(imageXRes*imageYRes*4,dtype=np.float32)
    chIndex = {"r":0,"g":1,"b":2,"a":3}
    for ch, src in spec.items():
        if isinstance(src,(int,float)):
            outPixelArray[chIndex[ch]::4] = src
        else:
            srcArr = _getSourcePixels(textureSetName,imageXRes,imageYRes,src[0])
            outPixelArray[chIndex[ch]::4] = srcArr[chIndex[src[1]]::4]
    outImg = _createOutImage(outImageName,imageXRes,imageYRes,True,"Non-Color")
    outImg.pixels.foreach_set(outPixelArray)
    return outImg
#-------------------
#Texture types packing
def packALB(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack ALB ----------------------------------------------
    albedoImage = _getSourceImage(textureSetName,imageXRes,imageYRes,"BaseColor")
    return albedoImage
def packALBA(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack ALBA ----------------------------------------------
    outPixelArray = _packRGBAlpha(textureSetName,imageXRes,imageYRes,outImageName,"BaseColor",("Opacity",0,False))
    outImg = _createOutImage(outImageName,imageXRes,imageYRes,False,"sRGB")
    outImg.pixels.foreach_set(outPixelArray)
    return outImg
def packALBD(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack ALBD ----------------------------------------------
    outPixelArray = _packRGBAlpha(textureSetName,imageXRes,imageYRes,outImageName,"BaseColor",("Metallic",0,True))#Invert the metallic
    outImg = _createOutImage(outImageName,imageXRes,imageYRes,False,"sRGB")
    outImg.pixels.foreach_set(outPixelArray)
    return outImg
def packALBM(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack ALBM ----------------------------------------------
    outPixelArray = _packRGBAlpha(textureSetName,imageXRes,imageYRes,outImageName,"BaseColor",("Metallic",0,False))
    outImg = _createOutImage(outImageName,imageXRes,imageYRes,False,"sRGB")
    outImg.pixels.foreach_set(outPixelArray)
    return outImg
def packALP(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack ALP ----------------------------------------------
    alphaImage = _getSourceImage(textureSetName,imageXRes,imageYRes,"Opacity")
    return alphaImage
def packEMI(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack EMI ----------------------------------------------
    outPixelArray = _packRGBAlpha(textureSetName,imageXRes,imageYRes,outImageName,"Emissive",0.0)#Black out the alpha channel of emission maps, this is because mh wilds uses the alpha channel of EMI as a mask for certain things. This doesn't affect other games.
    outImg = _createOutImage(outImageName,imageXRes,imageYRes,False,"sRGB")
    outImg.pixels.foreach_set(outPixelArray)
    return outImg
def packNRMR(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack NRMR ----------------------------------------------
    outPixelArray = _packRGBAlpha(textureSetName,imageXRes,imageYRes,outImageName,"Normal",("Roughness",0,False))
    outImg = _createOutImage(outImageName,imageXRes,imageYRes,True,"Non-Color")
    outImg.pixels.foreach_set(outPixelArray)
    return outImg
def packNRM(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack NRM ----------------------------------------------
    normalImage = _getSourceImage(textureSetName,imageXRes,imageYRes,"Normal")
    return normalImage

def packNROC(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack NROC ----------------------------------------------
    return _packNRX(textureSetName,imageXRes,imageYRes,outImageName,"AO",1.0)

def packNRRT(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack NRRT ----------------------------------------------
    return _packNRX(textureSetName,imageXRes,imageYRes,outImageName,"Roughness",0.0)

def packNRRC(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack NRRC ----------------------------------------------
    return _packNRX(textureSetName,imageXRes,imageYRes,outImageName,"Roughness",1.0)

def packNRRA(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack NRRA ----------------------------------------------
    return _packNRX(textureSetName,imageXRes,imageYRes,outImageName,"Roughness","Opacity")

def packNRRO(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack NRRO ----------------------------------------------
    return _packNRX(textureSetName,imageXRes,imageYRes,outImageName,"Roughness","AO")

def packATOS(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack ATOS ----------------------------------------------
    return _packChannelMix(textureSetName,imageXRes,imageYRes,outImageName,{"r":("Opacity",0),"g":1.0,"b":("AO",0),"a":1.0})
def packOCMA(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack OCMA ----------------------------------------------
    return _packChannelMix(textureSetName,imageXRes,imageYRes,outImageName,{"r":("AO",0),"g":1.0,"b":1.0,"a":("Opacity",0)})

def packSCOT(textureSetName,imageXRes,imageYRes,outImageName):
    #Pack SCOT ----------------------------------------------
    return _packChannelMix(textureSetName,imageXRes,imageYRes,outImageName,{"r":("AO",0),"g":1.0,"b":1.0,"a":0.0})
#-------------------

texTypeDict = {
	"alb":packALB,
	"alba":packALBA,
	"albd":packALBD,
	"albm":packALBM,
	"alp":packALP,
	"atoc":packATOS,
	"atos":packATOS,
	"acot":packATOS,
	"emi":packEMI,
	"nrm":packNRM,
	"nrmr":packNRMR,
	"nroc":packNROC,
	"nroe":packNROC,
	"nrof":packNROC,
	"nrrt":packNRRT,
	"nrrc":packNRRC,
	"nrrm":packNRRC,
	"nrro":packNRRO,
	"nrra":packNRRA,
	"ocma":packOCMA,
	"ocnm":packOCMA,
	"scot":packSCOT,
	}


def getTexturePacker(texType):
	return texTypeDict[texType.lower()]#If you get an error here and you're on the latest version, please report what material presets you used