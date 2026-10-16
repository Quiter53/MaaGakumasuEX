using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Avalonia.Media.Imaging;
using CommunityToolkit.Mvvm.ComponentModel;
using MFAAvalonia.Helper;
using Newtonsoft.Json;
using Newtonsoft.Json.Serialization;
using Newtonsoft.Json.Linq;

namespace MFAAvalonia.ViewModels.Pages;

public sealed class PlayCardRule
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string Card { get; set; } = "";
    public bool Enabled { get; set; } = true;
    public bool Limited { get; set; }
    public bool SingleCopy { get; set; }
    public int Min { get; set; } = 1;
    public int Max { get; set; } = 12;
    public string? Tier { get; set; }
    public string Mode { get; set; } = "linear";
    [JsonProperty(ObjectCreationHandling=ObjectCreationHandling.Replace)]
    public List<int[]> Points { get; set; } = [ [12,50], [1,50] ];
}
public sealed class PlayCardPreset
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N");
    public string Name { get; set; } = "";
    public string Career { get; set; } = "通用";
    public bool Builtin { get; set; }
    public List<PlayCardRule> Rules { get; set; } = [];
    [JsonProperty(ObjectCreationHandling=ObjectCreationHandling.Replace)]
    public List<string> Hand { get; set; } = ["ゆめみごこち", "せのびの魔法", "届いて！", "メリクリで～す♪", "イメトレ"];
    public override string ToString() => Builtin ? ExText.T(Name) : Name;
}
public sealed class PlayCardDocument
{
    public int Version { get; set; } = 2;
    public string Active { get; set; } = "career-2";
    public List<PlayCardPreset> Presets { get; set; } = [];
}
public sealed record PlayCardPreview(CardListCard Card, PlayCardRule? Rule, int Slot, bool Allowed, int Tier, double Priority);

public static class PlayCardPolicy
{
    public static readonly string[] Tiers = ["N", "R", "SR", "SSR", "LR"];
    public static readonly string[] Careers = ["好调", "集中", "干劲", "好印象", "强气", "全力"];
    public static readonly JsonSerializerSettings Json = new() { ContractResolver = new CamelCasePropertyNamesContractResolver(), Formatting = Formatting.Indented };
    public static string Key(string? name) => new((name ?? "").Trim().Replace('＋','+').Replace('！','!').Replace('？','?').Replace('〜','~').Replace('～','~').TrimEnd('+').Where(c => !char.IsWhiteSpace(c)).ToArray());
    public static bool Allowed(PlayCardRule? rule, int? turn) => rule == null || !rule.Enabled || !rule.Limited || (turn.HasValue && turn >= rule.Min && turn <= rule.Max);
    public static double Priority(PlayCardRule? rule, int? turn)
    {
        if (rule == null || !rule.Enabled || turn == null) return 50;
        var points = rule.Points.OrderByDescending(p => p[0]).ToArray();
        if (turn >= points[0][0]) return points[0][1];
        if (turn <= points[^1][0]) return points[^1][1];
        for (int i=0; i<points.Length-1; i++)
        {
            var a=points[i]; var b=points[i+1];
            if (turn > a[0] || turn < b[0]) continue;
            if (rule.Mode == "step") return turn == b[0] ? b[1] : a[1];
            return Math.Floor((a[1]+(b[1]-a[1])*(a[0]-turn.Value)/(double)(a[0]-b[0]))*10+.5)/10;
        }
        return 50;
    }
    public static int EffectiveTier(string rarity, PlayCardRule? rule)
    {
        int tier = Array.IndexOf(Tiers, rarity);
        return rule?.Enabled == true ? Math.Max(tier,Math.Min(3,Array.IndexOf(Tiers,rule.Tier))) : tier;
    }
    public static T Clone<T>(T value) => JsonConvert.DeserializeObject<T>(JsonConvert.SerializeObject(value,Json))!;
    public static PlayCardDocument Read(string text)
    {
        var doc=JObject.Parse(text);
        void Type(JToken? token,JTokenType type) { if(token?.Type!=type)throw new InvalidDataException("规则字段类型无效"); }
        Type(doc["version"],JTokenType.Integer);Type(doc["active"],JTokenType.String);Type(doc["presets"],JTokenType.Array);
        foreach(var p in (JArray)doc["presets"]!)
        {
            Type(p,JTokenType.Object);
            foreach(var key in new[]{"id","name","career"})Type(p[key],JTokenType.String);
            Type(p["builtin"],JTokenType.Boolean);Type(p["rules"],JTokenType.Array);Type(p["hand"],JTokenType.Array);
            foreach(var card in (JArray)p["hand"]!)Type(card,JTokenType.String);
            foreach(var r in (JArray)p["rules"]!)
            {
                Type(r,JTokenType.Object);foreach(var key in new[]{"id","card","mode"})Type(r[key],JTokenType.String);
                foreach(var key in new[]{"enabled","limited"})Type(r[key],JTokenType.Boolean);
                if(r["singleCopy"]!=null)Type(r["singleCopy"],JTokenType.Boolean);
                foreach(var key in new[]{"min","max"})Type(r[key],JTokenType.Integer);
                if(r["tier"]!=null&&r["tier"]!.Type!=JTokenType.Null)Type(r["tier"],JTokenType.String);
                Type(r["points"],JTokenType.Array);
                foreach(var point in (JArray)r["points"]!){Type(point,JTokenType.Array);foreach(var number in (JArray)point)Type(number,JTokenType.Integer);}
            }
        }
        return doc.ToObject<PlayCardDocument>() ?? throw new InvalidDataException("规则文件为空");
    }
    public static void Validate(PlayCardDocument doc, IReadOnlyList<CardListCard> cards)
    {
        void Require(bool value,string message) { if(!value) throw new InvalidDataException(message); }
        Require(doc.Version==2,"不支持的规则版本");
        Require(doc.Presets is { Count: >0 and <=200 },"预设列表无效");
        var ids=new HashSet<string>(); var names=new HashSet<string>(); var catalog=cards.Select(c=>c.Name).ToHashSet();
        foreach(var p in doc.Presets)
        {
            Require(p != null && !string.IsNullOrWhiteSpace(p.Id) && p.Id.Length<=100 && ids.Add(p.Id),"预设 ID 重复或无效");
            Require(!string.IsNullOrWhiteSpace(p.Name) && p.Name.Length<=30 && names.Add(p.Name),"预设名称重复或无效");
            Require(Careers.Contains(p.Career)||p.Career=="通用","职业无效");
            Require(!p.Builtin || (p.Name==p.Career && p.Id=="career-"+Array.IndexOf(Careers,p.Career)),"内置预设不可更名");
            Require(p.Hand is {Count: >=1 and <=5} && p.Hand.All(catalog.Contains),"预览手牌应为 1–5 张有效卡牌");
            Require(p.Rules != null && p.Rules.Count<=1000,"规则列表无效");
            var ruleIds=new HashSet<string>(); var cardKeys=new HashSet<string>();
            foreach(var r in p.Rules)
            {
                Require(r != null && !string.IsNullOrWhiteSpace(r.Id) && ruleIds.Add(r.Id),"规则 ID 重复或无效");
                Require(catalog.Contains(r.Card) && cardKeys.Add(Key(r.Card)),"规则卡牌无效或重复");
                Require(r.Min>=1 && r.Max<=30 && r.Min<=r.Max,"使用回合区间无效");
                Require(r.Mode is "linear" or "step","曲线类型无效");
                Require(r.Tier==null || Tiers.Contains(r.Tier),"排序档位无效");
                Require(r.Points is {Count: >=2 and <=30} && r.Points.All(p=>p is {Length:2} && p[0]>=1 && p[0]<=30 && p[1]>=0 && p[1]<=100),"曲线节点无效");
                Require(r.Points.Select(p=>p[0]).Distinct().Count()==r.Points.Count,"曲线节点回合重复");
            }
        }
        Require(ids.Contains(doc.Active),"当前预设不存在");
        Require(Enumerable.Range(0,6).All(i=>doc.Presets.Any(p=>p.Id=="career-"+i && p.Builtin)),"内置职业预设缺失");
    }
}

public sealed class CardRulesViewModel : ObservableObject, IDisposable
{
    private readonly string _root;
    private string _revision = "missing";
    private string _status = "";
    private bool _dirty;
    public string Status { get=>_status; private set=>SetProperty(ref _status,value); }
    public bool Dirty { get=>_dirty; private set=>SetProperty(ref _dirty,value); }
    public bool Loaded { get; private set; }
    public PlayCardDocument Document { get; private set; } = new();
    public List<CardListCard> Cards { get; } = [];
    public PlayCardPreset Preset => Document.Presets.First(p=>p.Id==Document.Active);
    public string? SelectedId { get; set; }
    public PlayCardRule? Selected => Loaded ? Preset.Rules.FirstOrDefault(r=>r.Id==SelectedId) : null;
    public int PreviewTurn { get; set; } = 5;
    public string ConfigPath => Path.Combine(_root,"config","card_rules.json");
    public CardRulesViewModel() : this(AppPaths.InstallRoot) { }
    public CardRulesViewModel(string root)
    {
        _root=root;
        try
        {
            var library=JsonConvert.DeserializeObject<CardLibraryDocument>(File.ReadAllText(Path.Combine(root,"config","card_library.json")))!;
            Cards.AddRange(library.Cards);
            Load();
        }
        catch(Exception ex) { Status="读取失败："+ex.Message; }
    }
    public CardListCard Card(string name) => Cards.First(c=>c.Name==name);
    public Bitmap? Image(CardListCard card)
    {
        if(card.Thumbnail!=null) return card.Thumbnail;
        var path=Path.Combine(_root,card.Image.Replace('/',Path.DirectorySeparatorChar));
        if(!File.Exists(path))path=Path.Combine(_root,"assets",card.Image);
        if(File.Exists(path)) try { using var stream=File.OpenRead(path);card.Thumbnail=Bitmap.DecodeToWidth(stream,112); } catch { }
        return card.Thumbnail;
    }
    public bool Load()
    {
        try
        {
            var path=File.Exists(ConfigPath)?ConfigPath:Path.Combine(_root,"agent","custom","card_rules.defaults.json");
            var document=PlayCardPolicy.Read(File.ReadAllText(path));
            PlayCardPolicy.Validate(document,Cards);
            Document=document;_revision=Revision();Loaded=true;Dirty=false;
            SelectedId=Preset.Rules.FirstOrDefault()?.Id;
            Status=_revision=="missing"?"默认规则 · 待保存":"已载入 · 当前预设生效";
            return true;
        }
        catch(Exception ex) { Status="读取失败，原文件保留："+ex.Message;return false; }
    }
    private string Revision() => File.Exists(ConfigPath)?Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(ConfigPath))):"missing";
    public void Mark() { Dirty=true;Status="有未保存修改"; }
    public bool Save()
    {
        if(!Loaded)return false;
        string? temporary=null; FileStream? fileLock=null;
        var lockPath=Path.ChangeExtension(ConfigPath,"lock");
        try
        {
            PlayCardPolicy.Validate(Document,Cards);
            Directory.CreateDirectory(Path.GetDirectoryName(ConfigPath)!);
            fileLock=new FileStream(lockPath,FileMode.CreateNew,FileAccess.Write,FileShare.None);
            if(_revision!=Revision())throw new IOException("规则已被其他窗口修改，请重新载入后编辑。当前修改仍保留。");
            if(File.Exists(ConfigPath))
            {
                PlayCardPolicy.Validate(PlayCardPolicy.Read(File.ReadAllText(ConfigPath)),Cards);
                File.Copy(ConfigPath,ConfigPath+".bak",true);
            }
            temporary=ConfigPath+"."+Guid.NewGuid().ToString("N")+".tmp";
            using(var stream=new FileStream(temporary,FileMode.CreateNew,FileAccess.Write,FileShare.None))
            {
                var bytes=System.Text.Encoding.UTF8.GetBytes(JsonConvert.SerializeObject(Document,PlayCardPolicy.Json));
                stream.Write(bytes);stream.Flush(true);
            }
            File.Move(temporary,ConfigPath,true);_revision=Revision();Dirty=false;Status="已保存 · 当前预设生效";return true;
        }
        catch(Exception ex) { Status="保存失败："+ex.Message;return false; }
        finally
        {
            if(temporary!=null && File.Exists(temporary))File.Delete(temporary);
            if(fileLock!=null) { fileLock.Dispose();File.Delete(lockPath); }
        }
    }
    public void SwitchPreset(string id)
    {
        Document.Active=id;SelectedId=Preset.Rules.FirstOrDefault()?.Id;Mark();
    }
    public void AddRule(string name)
    {
        var existing=Preset.Rules.FirstOrDefault(r=>PlayCardPolicy.Key(r.Card)==PlayCardPolicy.Key(name));
        if(existing!=null){SelectedId=existing.Id;return;}
        var rule=new PlayCardRule { Card=name,Tier=Card(name).Rarity };Preset.Rules.Add(rule);SelectedId=rule.Id;Mark();
    }
    public void RemoveRule() { if(Selected is {} r) Preset.Rules.Remove(r);SelectedId=Preset.Rules.FirstOrDefault()?.Id;Mark(); }
    public void SetHand(int slot,string name)
    {
        _=Card(name);
        if(slot<0||slot>4||slot>Preset.Hand.Count)throw new ArgumentOutOfRangeException(nameof(slot));
        if(slot==Preset.Hand.Count)Preset.Hand.Add(name);else Preset.Hand[slot]=name;Mark();
    }
    public void RemoveHand(int slot) { if(Preset.Hand.Count>1) { Preset.Hand.RemoveAt(slot);Mark(); } }
    public void EditPreset(string name,string career,bool create,bool copy)
    {
        name=name.Trim();
        if(name.Length is <1 or >30 || Document.Presets.Any(p=>p.Name==name&&(create||p.Id!=Document.Active)))throw new InvalidDataException("预设名称为空、过长或与已有预设重复");
        if(!PlayCardPolicy.Careers.Contains(career)&&career!="通用")throw new InvalidDataException("职业无效");
        if(create)
        {
            var next=copy?PlayCardPolicy.Clone(Preset):new PlayCardPreset();
            next.Id=Guid.NewGuid().ToString("N");next.Name=name;next.Career=career;next.Builtin=false;
            Document.Presets.Add(next);Document.Active=next.Id;SelectedId=next.Rules.FirstOrDefault()?.Id;
        }
        else if(!Preset.Builtin){Preset.Name=name;Preset.Career=career;}
        Mark();
    }
    public void DeletePreset() { if(Preset.Builtin)return;Document.Presets.Remove(Preset);SwitchPreset(Document.Presets[0].Id); }
    public bool SetPoint(int index,int turn,int score)
    {
        var rule=Selected!;
        if(turn is <1 or >30 || score is <0 or >100 || rule.Points.Where((_,i)=>i!=index).Any(p=>p[0]==turn))return false;
        rule.Points[index]=[turn,score];Mark();return true;
    }
    public void AddPoint()
    {
        var r=Selected!;var turn=Enumerable.Range(1,30).OrderBy(t=>Math.Abs(t-6)).FirstOrDefault(t=>r.Points.All(p=>p[0]!=t));
        if(turn==0)return;r.Points.Add([turn,(int)Math.Round(PlayCardPolicy.Priority(r,turn))]);r.Points=r.Points.OrderByDescending(p=>p[0]).ToList();Mark();
    }
    public List<PlayCardPreview> Preview() => Preset.Hand.Select((name,slot)=>
    {
        var c=Card(name);var r=Preset.Rules.FirstOrDefault(r=>PlayCardPolicy.Key(r.Card)==PlayCardPolicy.Key(name));
        return new PlayCardPreview(c,r,slot,PlayCardPolicy.Allowed(r,PreviewTurn),PlayCardPolicy.EffectiveTier(c.Rarity,r),PlayCardPolicy.Priority(r,PreviewTurn));
    }).OrderByDescending(p=>p.Allowed).ThenByDescending(p=>p.Card.Extra).ThenByDescending(p=>p.Tier).ThenByDescending(p=>p.Priority).ThenBy(p=>p.Slot).ToList();
    public List<CardListCard> Filter(string query,int rarity,int plan,int category,int origin,int conversion,int extra,int unlock,int sort)
    {
        string[] rarities=["","LR","SSR","SR","R","N"];string[] origins=["","none","idol","support"];
        var ranges=new[]{(0,999),(0,0),(1,20),(21,40),(41,60),(61,999)};
        var rows=Cards.Where(c=>c.Name.Contains(query.Trim(),StringComparison.OrdinalIgnoreCase)
            && (rarity==0||c.Rarity==rarities[rarity]) && (plan==0||c.PlanType==plan)
            && (category==0||c.Category==category) && (origin==0||c.Origin==origins[origin])
            && (conversion==0||c.IsConversion==(conversion==2)) && (extra==0||c.Extra==(extra==1))
            && c.UnlockProducerLevel>=ranges[unlock].Item1 && c.UnlockProducerLevel<=ranges[unlock].Item2);
        return (sort==1?rows.OrderBy(c=>c.UnlockProducerLevel):sort==2?rows.OrderByDescending(c=>c.UnlockProducerLevel):rows).ToList();
    }
    public void Dispose() { foreach(var card in Cards){card.Thumbnail?.Dispose();card.Thumbnail=null;} }
}
