"""Diamond memory traits: UR frames/row art extracted from the installed game.

SSR rainbow frames and blue skill-effect diamonds are separate assets.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]/'resource/base/image/produce/memory'


def matches(context, image, paths, roi, *, method=1, threshold=.025):
    node='ProduceMemoryAbilityFrame'
    detail=context.run_recognition(node,image,{node:{'recognition':'TemplateMatch','template':paths,
        'roi':roi,'green_mask':True,'method':method,'threshold':threshold,'order_by':'Vertical'}})
    if not detail or not detail.hit:return []
    return [r for r in detail.filtered_results or [] if (r.score<=threshold if method==1 else r.score>=threshold)]


def summary_diamond(context, image):
    """Three miniature frames are always visible above the summary scroll pane."""
    found=[]
    for rarity in ('r','sr','ssr','ur'):
        for row in matches(context,image,[str(ROOT/f'ability_{rarity}_64.png')],[105,305,245,100]):
            x,y,w,h=map(int,row.box)
            if any(abs(x-a[0])<20 for a in found):continue
            found.append((x,rarity))
    if len(found)!=3:return None
    return any(rarity=='ur' for _,rarity in found)


def diamond_row(context, image, top, bottom):
    if bottom-top<82:return False
    paths=[str(ROOT/f'ability_ur_row_{height}.png') for height in (82,86,90,96,110,123) if height<=bottom-top]
    return bool(matches(context,image,paths,[65,top,600,bottom-top],method=5,threshold=.67))
