import unittest
from engine import resolve_identity_hints
from lyrics_engine import automatic_provider_order, is_probably_hebrew_identity

class HebrewAutomaticPipelineTests(unittest.TestCase):
    def test_hebrew_prefers_musixmatch_when_configured(self):
        order=automatic_provider_order('יום חדש','רביד פלוטניק','he',True,False)
        self.assertEqual(order[0],'musixmatch'); self.assertEqual(order[-1],'lrclib')
    def test_hebrew_without_musixmatch_falls_back_to_lrclib(self):
        self.assertEqual(automatic_provider_order('יום חדש','רביד פלוטניק','auto',False,False),['lyrics.ovh','lrclib'])
    def test_english_keeps_lrclib_first(self):
        self.assertEqual(automatic_provider_order('Hello','Artist','en',True,False)[:3],['lyrics.ovh','lrclib','musixmatch'])
    def test_hebrew_identity_detection_from_text(self):
        self.assertTrue(is_probably_hebrew_identity("עזבת ת'בית",'פלד','auto'))
    def test_identity_priority_url_over_metadata_and_filename(self):
        title,artist,source=resolve_identity_hints(None,None,{'title':'יום חדש','artist':'Ravid Plotnik'},{'title':'Wrong','artist':'Wrong'},{'title':'Other','artist':'Other'})
        self.assertEqual((title,artist,source),('יום חדש','Ravid Plotnik','musixmatch-url'))
    def test_manual_identity_is_highest_priority(self):
        title,artist,source=resolve_identity_hints('Manual Song','Manual Artist',{'title':'URL Song','artist':'URL Artist'},{'title':'Meta Song','artist':'Meta Artist'},{'title':'File Song','artist':'File Artist'})
        self.assertEqual((title,artist,source),('Manual Song','Manual Artist','manual'))
if __name__=='__main__': unittest.main()
