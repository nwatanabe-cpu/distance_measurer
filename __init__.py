# __init__.py の中身
def classFactory(iface):
    # distance_measurer.py 内の DistanceMeasurePlugin クラスを読み込む
    from .distance_measurer import DistanceMeasurePlugin
    return DistanceMeasurePlugin(iface)