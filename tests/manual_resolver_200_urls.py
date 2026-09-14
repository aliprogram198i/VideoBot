import asyncio
from urllib.parse import urlparse

RAW = '''
https://www.youtube.com/shorts/3f40fN13U8M
https://www.youtube.com/shorts/5e_S85qI47s
https://www.youtube.com/watch?v=aqz-KE-bpKQ
https://www.youtube.com/watch?v=L_LUpnjgPso
https://youtu.be/M7lc1UVf-VE
https://www.youtube.com/watch?v=fJ9rUzIMcZQ
https://www.youtube.com/shorts/CevxZvSJLk8
https://www.youtube.com/watch?v=21X5lGlDOfg
https://www.youtube.com/shorts/jNQXAC9IVRw
https://www.youtube.com/shorts/dQw4w9WgXcQ
https://www.youtube.com/watch?v=kffacxfA7G4
https://www.youtube.com/shorts/9bZkp7q19f0
https://www.youtube.com/watch?v=kJQP7kiw5Fk
https://www.youtube.com/shorts/JGwWNGJdvx8
https://www.youtube.com/shorts/OPf0YbXqDm0
https://www.youtube.com/watch?v=RgKAFK5djSk
https://youtu.be/jNQXAC9IVRw?t=5s
https://youtu.be/dQw4w9WgXcQ?feature=shared
https://youtu.be/kJQP7kiw5Fk?si=abc123xyz
https://youtu.be/kffacxfA7G4?t=12
https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share
https://www.youtube.com/shorts/OPf0YbXqDm0?si=test1234
https://m.youtube.com/watch?v=kJQP7kiw5Fk
https://m.youtube.com/watch?v=fJ9rUzIMcZQ
https://www.youtube.com/watch?v=e-ORhEE9VVg
https://www.youtube.com/watch?v=fLexgOxsZu0
https://www.youtube.com/watch?v=ZbZSe6N_BXs
https://www.youtube.com/watch?v=3JZ_D3ELwOQ
https://www.youtube.com/watch?v=l482T0yNkeo
https://www.youtube.com/watch?v=hT_nvWreIhg
https://www.youtube.com/watch?v=09R8_2nJtjg
https://www.youtube.com/watch?v=V-_O7nl0Ii0
https://www.youtube.com/watch?v=uelHwf8o7_U
https://www.youtube.com/watch?v=YQHsXMglC9A
https://www.youtube.com/shorts/uelHwf8o7_U
https://www.youtube.com/shorts/YQHsXMglC9A
https://www.youtube.com/shorts/ZbZSe6N_BXs
https://www.youtube.com/shorts/e-ORhEE9VVg
https://youtu.be/fLexgOxsZu0
https://youtu.be/ZbZSe6N_BXs?t=30s
https://youtu.be/3JZ_D3ELwOQ?feature=share
https://www.youtube.com/watch?v=C0DPdy98e4c
https://www.youtube.com/shorts/C0DPdy98e4c
https://youtu.be/C0DPdy98e4c
https://www.youtube.com/watch?v=kJQP7kiw5Fk&list=PL12345
https://www.youtube.com/shorts/3f40fN13U8M?feature=share
https://www.youtube.com/shorts/5e_S85qI47s?si=track098
https://youtu.be/21X5lGlDOfg?t=45
https://www.youtube.com/watch?v=aqz-KE-bpKQ&t=1m
https://www.youtube.com/shorts/CevxZvSJLk8?feature=share
https://www.tiktok.com/@zachking/video/6768504823336815877
https://www.tiktok.com/@khaby.lame/video/6987625902148259077
https://www.tiktok.com/@bellapoarch/video/6862153058223197445
https://www.tiktok.com/@mrbeast/video/7154439003503201582
https://www.tiktok.com/@charlidamelio/video/6789423605655784710
https://www.tiktok.com/@gordonramsayofficial/video/6883738596637953285
https://www.tiktok.com/@addisonre/video/6803248386343718149
https://www.tiktok.com/@redbull/video/7101889498266651910
https://www.tiktok.com/@nba/video/7016259203115453702
https://www.tiktok.com/@willsmith/video/6835150917029956870
https://www.tiktok.com/@420doggface208/video/6876424179084709126
https://www.tiktok.com/@khaby.lame/video/6949318182962056453
https://www.tiktok.com/@khaby.lame/video/6954993827201158405
https://www.tiktok.com/@mrbeast/video/7097782337651526958
https://vm.tiktok.com/ZMhFv1Yqa/
https://vm.tiktok.com/ZMhFv9Kbc/
https://vm.tiktok.com/ZMhFvP82x/
https://vm.tiktok.com/ZMhFvR14q/
https://vm.tiktok.com/ZMhFvX99z/
https://vt.tiktok.com/ZS2xN8y7L/
https://vt.tiktok.com/ZS2xNE74A/
https://vt.tiktok.com/ZS2xN6KpM/
https://vt.tiktok.com/ZS2xN5RvT/
https://vt.tiktok.com/ZS2xN99Qa/
https://www.tiktok.com/@zachking/video/6768504823336815877?is_from_webapp=1
https://www.tiktok.com/@khaby.lame/video/6987625902148259077?is_from_webapp=1&sender_device=pc
https://www.tiktok.com/@bellapoarch/video/6862153058223197445?sender_device=mobile&share_id=1
https://www.tiktok.com/@mrbeast/video/7154439003503201582?lang=en
https://www.tiktok.com/@mrbeast/video/7154439003503201582?lang=ar
https://www.tiktok.com/@charlidamelio/video/6789423605655784710?_r=1
https://www.tiktok.com/@gordonramsayofficial/video/6883738596637953285?is_copy_url=1
https://www.tiktok.com/@addisonre/video/6803248386343718149?is_from_webapp=1
https://www.tiktok.com/@redbull/video/7101889498266651910?is_copy_url=1
https://www.tiktok.com/@nba/video/7016259203115453702?sender_device=pc
https://www.tiktok.com/@willsmith/video/6835150917029956870?_r=1&lang=en
https://www.tiktok.com/@420doggface208/video/6876424179084709126?share_app_id=1233
https://www.tiktok.com/@khaby.lame/video/6949318182962056453?source=h5_m
https://www.tiktok.com/@spencerx/video/6809823485721832709
https://www.tiktok.com/@justmaiko/video/6817294821033481477
https://www.tiktok.com/@dixiedamelio/video/6842183921094823173
https://www.tiktok.com/@lorengray/video/6821948219482194821
https://www.tiktok.com/@cznburak/video/6854932019482194823
https://www.tiktok.com/@bayashi.tiktok/video/7123984219482194825
https://www.tiktok.com/@scottyhuss/video/6912384912839481234
https://www.tiktok.com/@thebentist/video/6982348912349182349
https://www.tiktok.com/@devonrodriguezart/video/7034821948192384912
https://vm.tiktok.com/ZMhFvA12b/
https://vt.tiktok.com/ZS2xN11Aa/
https://www.tiktok.com/@spencerx/video/6809823485721832709?lang=en
https://www.tiktok.com/@justmaiko/video/6817294821033481477?is_from_webapp=1
https://www.instagram.com/reel/C8r4P80yJ12/
https://www.instagram.com/reel/C5s7QzrvP-H/
https://www.instagram.com/reel/C01r9oVv2_L/
https://www.instagram.com/reel/Cz3m8kYtx4z/
https://www.instagram.com/p/CL9xZ35rN_1/
https://www.instagram.com/p/B90ogJuJ7wG/
https://www.instagram.com/p/B_8Z9Ptp0Qf/
https://www.instagram.com/reel/Cy8o4qpvM8a/
https://www.instagram.com/reel/Cw9k6v-v1-x/
https://www.instagram.com/reel/C2-7fE0v4Pj/?igsh=MWQ1ZGUxMzBkMA==
https://www.instagram.com/reel/C3bW9mBv0hS/
https://www.instagram.com/reel/C1kU9w9r7xZ/
https://www.instagram.com/reel/C8r4P80yJ12/?utm_source=ig_web_copy_link
https://www.instagram.com/reel/C5s7QzrvP-H/?igsh=YWNyOG94M211
https://www.instagram.com/reel/C01r9oVv2_L/?utm_medium=copy_link
https://www.instagram.com/p/CL9xZ35rN_1/?igsh=aWduOXBwM214
https://www.instagram.com/p/B90ogJuJ7wG/?utm_source=ig_web_button_share_sheet
https://www.instagram.com/reel/C2-7fE0v4Pj/
https://www.instagram.com/reel/C9aBcDeFgHi/
https://www.instagram.com/reel/C9bCdEfGhIj/
https://www.instagram.com/reel/C9cDeFgHiJk/
https://www.instagram.com/reel/C9dEfGhIjKl/
https://www.instagram.com/reel/C9eFgHiJkLm/
https://www.instagram.com/reel/C9fGhIjKlMn/
https://www.instagram.com/reel/C9gHiJkLmNo/
https://www.instagram.com/p/C9hIjKlMnOp/
https://www.instagram.com/p/C9jKlMnOpQr/
https://www.instagram.com/p/C9kLmNoPqRs/
https://www.instagram.com/p/C9lMnOpQrSt/
https://www.instagram.com/p/C9mNoPqRsTu/
https://www.instagram.com/tv/B90ogJuJ7wG/
https://www.instagram.com/tv/CL9xZ35rN_1/
https://www.instagram.com/tv/C5s7QzrvP-H/
https://www.instagram.com/reel/C8r4P80yJ12/?igsh=NTc4MTIwNjQ2YQ==
https://www.instagram.com/reel/Cz3m8kYtx4z/?igsh=MWZmaGxh
https://www.instagram.com/reel/Cy8o4qpvM8a/?igsh=bTNpMWJ1
https://www.instagram.com/reel/Cw9k6v-v1-x/?igsh=c2hhcmVsaW5r
https://www.instagram.com/reel/C3bW9mBv0hS/?igsh=aWdfb3JpZw==
https://www.instagram.com/reel/C1kU9w9r7xZ/?igsh=MW0ydHF6
https://www.instagram.com/reel/C-a1B2c3D4e/
https://www.instagram.com/reel/C-b2C3d4E5f/
https://www.instagram.com/reel/C-c3D4e5F6g/
https://www.instagram.com/reel/C-d4E5f6G7h/
https://www.instagram.com/reel/C-e5F6g7H8i/
https://www.instagram.com/p/C-f6G7h8I9j/
https://www.instagram.com/p/C-g7H8i9J0k/
https://www.instagram.com/p/C-h8I9j0K1l/
https://www.instagram.com/p/C-i9J0k1L2m/
https://www.instagram.com/reel/C-j0K1l2M3n/
https://www.instagram.com/reel/C-k1L2m3N4o/
https://www.facebook.com/reel/738291048392019
https://www.facebook.com/watch/?v=10153231379946729
https://www.facebook.com/reel/10224558291823901
https://www.facebook.com/NASA/videos/10155799982461772/
https://www.facebook.com/reel/892347102194820
https://m.facebook.com/watch/?v=10154485324126729
https://m.facebook.com/watch/?v=987654321012345
https://www.facebook.com/watch/?v=356889218320491
https://www.facebook.com/watch/?v=10156123456789012
https://www.facebook.com/reel/10158226999999999
https://www.facebook.com/Meta/videos/10158947265286290/
https://fb.watch/uX8k3q_vLm/
https://fb.watch/uX8k9p_aBc/
https://fb.watch/uX8k1z_dEf/
https://fb.watch/uX8k7y_gHi/
https://fb.watch/uX8k5x_jKl/
https://www.facebook.com/reel/738291048392019?s=yWDuG2&fs=e
https://www.facebook.com/reel/10224558291823901?mibextid=0cALme
https://www.facebook.com/reel/892347102194820?mibextid=rS40aB7S9Ucbxw6v
https://www.facebook.com/reel/10158226999999999?s=yWDuG2
https://m.facebook.com/story.php?story_fbid=10155799982461772&id=1000000000
https://m.facebook.com/reel/738291048392019
https://m.facebook.com/reel/10224558291823901
https://www.facebook.com/SpaceX/videos/10156789012345678/
https://www.facebook.com/NationalGeographic/videos/10157890123456789/
https://www.facebook.com/BBCNews/videos/10158901234567890/
https://www.facebook.com/watch/?ref=saved&v=10153231379946729
https://www.facebook.com/watch/?ref=external&v=356889218320491
https://www.facebook.com/reel/1122334455667788
https://www.facebook.com/reel/2233445566778899
https://www.facebook.com/reel/3344556677889900
https://www.facebook.com/watch/?v=4455667788990011
https://www.facebook.com/watch/?v=5566778899001122
https://m.facebook.com/watch/?v=6677889900112233
https://fb.watch/vY9l0w_mNo/
https://x.com/NASA/status/1803867772648759392
https://twitter.com/SpaceX/status/1768269785197543888
https://x.com/SpaceX/status/1768269785197543888
https://twitter.com/NASA/status/1803867772648759392
https://x.com/elonmusk/status/1785023958928625801
https://x.com/OpenAI/status/1790072081548177695
https://twitter.com/OpenAI/status/1790072081548177695
https://x.com/Google/status/1790432849182390123
https://x.com/NatGeo/status/1774092837482910291
https://twitter.com/NatGeo/status/1774092837482910291
https://x.com/BBCBreaking/status/1789234891238491234
https://x.com/Reuters/status/1788293849182390123
https://twitter.com/Reuters/status/1788293849182390123
https://x.com/Tesla/status/1770982348912349182
https://x.com/archillect/status/1781293849182390123
'''

TEST_URLS=[x.strip() for x in RAW.splitlines() if x.strip()]
assert len(TEST_URLS)==200, f'Expected 200, got {len(TEST_URLS)}'

def platform(url):
    h=(urlparse(url).hostname or '').lower()
    if 'youtube' in h or h=='youtu.be': return 'youtube'
    if 'tiktok' in h: return 'tiktok'
    if 'instagram' in h: return 'instagram'
    if 'facebook' in h or h=='fb.watch': return 'facebook'
    if h in {'x.com','twitter.com'}: return 'x'
    return 'other'

async def main():
    import bot
    fn=getattr(bot,'extract_direct_media_urls',None)
    if not callable(fn): raise RuntimeError('extract_direct_media_urls unavailable')
    totals={p:{'attempted':0,'success':0,'empty':0,'errors':0} for p in ('youtube','tiktok','instagram','facebook','x','other')}
    failures=[]
    for i,url in enumerate(TEST_URLS,1):
        p=platform(url); totals[p]['attempted']+=1
        try:
            r=fn(url)
            if hasattr(r,'__await__'): r=await r
            if isinstance(r,(list,tuple)) and r: totals[p]['success']+=1
            else: totals[p]['empty']+=1
        except Exception as e:
            totals[p]['errors']+=1; failures.append((i,p,type(e).__name__,str(e)[:240]))
        print(f'[{i}/200] {p}',flush=True)
    print('===FINAL===',flush=True); print(totals,flush=True); print('failures=',len(failures),flush=True)
    for row in failures: print(row,flush=True)

if __name__=='__main__': asyncio.run(main())
